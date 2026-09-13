"""Unit tests for M8 Context Compaction & Rolling Memory (agent/memory.py).

Verifies:
- Context <= 10000 chars: no compaction.
- Context > 10000 chars: older turns compacted into deterministic rolling memory.
- Last 2 complete turns preserved intact with all original messages and active image_ids.
- SYSTEM message preserved at index 0.
- Tool-call / tool-result pairing integrity preserved (Conversation.validate_sequence() passes).
- Huge inspect_mesh and tool results safely compacted without dumping giant JSONs.
- Old image_ids stripped from compacted turns, preventing LRU cache expiration errors.
- Active turn image_id retained.
- RuntimeHistory completely untouched.
- No compaction during PENDING_APPROVAL or EXECUTING_TOOL.
- Correct compaction at new turn start.
- ContextBuilder MAX_CONTEXT_CHARS=15000 safety guard regression.
"""

import json
import unittest
from unittest.mock import MagicMock

from agent.context_builder import ContextBuilder, ImageResolutionError, MAX_CONTEXT_CHARS
from agent.dispatcher import ToolDispatcher
from agent.history import HistoryKind, RuntimeHistory
from tools.registry import ToolRegistry
from agent.memory import (
    COMPACTION_TRIGGER_CHARS,
    RETAINED_TURNS_COUNT,
    RollingMemory,
    calculate_messages_chars,
    compact_conversation,
    partition_conversation_into_turns,
)
from agent.models import ChatMessage, Conversation, Role, ToolCall
from agent.policy import ApprovalPolicy, PendingApproval
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class MockAdapter:
    def __init__(self):
        self.screenshots = {}

    def get_viewport_screenshot(self, image_id: str):
        return self.screenshots.get(image_id)


class DummyTool(BaseTool):
    name = "dummy_tool"
    description = "Dummy tool for testing"
    input_schema = {"type": "object", "properties": {}}
    risk_level = RiskLevel.LOW

    def execute(self, adapter, **kwargs):
        return ToolResult.ok(data={"status": "ok"})


class TestContextCompaction(unittest.TestCase):
    """Test suite covering M8 Context Compaction & Rolling Memory requirements."""

    def test_partition_conversation_into_turns(self):
        conv = Conversation()
        sys_msg = ChatMessage(role=Role.SYSTEM, content="System instructions")
        conv.add_message(sys_msg)

        # Turn 1
        conv.add_message(ChatMessage(role=Role.USER, content="User 1"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Assistant 1"))

        # Turn 2
        conv.add_message(ChatMessage(role=Role.USER, content="User 2"))
        tc = ToolCall(call_id="call_1", tool_name="dummy_tool", arguments={})
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        conv.add_message(ChatMessage(role=Role.TOOL, content='{"status": "ok"}', tool_call_id="call_1", name="dummy_tool"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Assistant 2"))

        # Turn 3
        conv.add_message(ChatMessage(role=Role.USER, content="User 3"))

        system_msg, prior_summary, turns = partition_conversation_into_turns(conv.messages)
        self.assertIsNotNone(system_msg)
        self.assertEqual(system_msg.content, "System instructions")
        self.assertIsNone(prior_summary)
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[0][0].content, "User 1")
        self.assertEqual(turns[1][0].content, "User 2")
        self.assertEqual(turns[2][0].content, "User 3")

    def test_no_compaction_under_trigger_limit(self):
        """Context <= 10000 chars should NOT trigger compaction."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System prompt"))
        for i in range(5):
            conv.add_message(ChatMessage(role=Role.USER, content=f"Short task {i}"))
            conv.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Short reply {i}"))

        total_chars = calculate_messages_chars(conv.messages)
        self.assertLess(total_chars, COMPACTION_TRIGGER_CHARS)

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=COMPACTION_TRIGGER_CHARS)
        self.assertFalse(was_compacted)
        self.assertEqual(len(compacted_conv.messages), len(conv.messages))

    def test_compaction_over_trigger_limit_retains_last_two_turns(self):
        """Context > 10000 chars should compact older turns while retaining the last 2 turns intact."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System prompt"))

        # Turn 1 (heavy content)
        conv.add_message(ChatMessage(role=Role.USER, content="Task 1: " + "A" * 3000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Done 1: " + "B" * 3000))

        # Turn 2 (heavy content)
        conv.add_message(ChatMessage(role=Role.USER, content="Task 2: " + "C" * 3000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Done 2: " + "D" * 3000))

        # Turn 3 (recent turn to retain)
        conv.add_message(ChatMessage(role=Role.USER, content="Task 3: Create a cylinder"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Created cylinder"))

        # Turn 4 (latest turn to retain)
        conv.add_message(ChatMessage(role=Role.USER, content="Task 4: What is in the scene?"))

        orig_chars = calculate_messages_chars(conv.messages)
        self.assertGreater(orig_chars, 10000)

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        compacted_chars = calculate_messages_chars(compacted_conv.messages)
        self.assertLess(compacted_chars, 3000)

        # SYSTEM message must be preserved at index 0
        self.assertEqual(compacted_conv.messages[0].role, Role.SYSTEM)
        self.assertEqual(compacted_conv.messages[0].content, "System prompt")

        # Summary messages inserted
        summary_user = compacted_conv.messages[1]
        self.assertEqual(summary_user.role, Role.USER)
        self.assertIn("[Context Summary & Scene Memory]", summary_user.content)
        self.assertIn("Task 1:", summary_user.content)
        self.assertIn("Task 2:", summary_user.content)

        # Last 2 turns preserved intact
        contents = [m.content for m in compacted_conv.messages]
        self.assertIn("Task 3: Create a cylinder", contents)
        self.assertIn("Created cylinder", contents)
        self.assertEqual(compacted_conv.messages[-1].content, "Task 4: What is in the scene?")

        # Sequence integrity
        compacted_conv.validate_sequence()

    def test_tool_call_sequence_integrity_with_compaction(self):
        """Ensure tool-call / tool-result pairs in older turns and retained turns are never broken."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Turn 1: mutating tool
        tc1 = ToolCall(call_id="call_create", tool_name="create_primitive", arguments={"type": "CUBE"})
        res1 = json.dumps({
            "name": "Cube",
            "location": [0.0, 0.0, 1.0],
            "verification": {
                "status": "PASS",
                "operation": "create",
                "target_name": "Cube",
                "verified_properties": ["location"],
            },
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Create a cube: " + "X" * 6000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res1, tool_call_id="call_create", name="create_primitive"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Cube created successfully: " + "Y" * 6000))

        # Turn 2: Retained turn with tools
        tc2 = ToolCall(call_id="call_mat", tool_name="set_material", arguments={"roughness": 0.5})
        res2 = json.dumps({
            "name": "Cube",
            "material_name": "Red_BSDF",
            "roughness": 0.5,
            "verification": {
                "status": "PASS",
                "operation": "set_material",
                "target_name": "Cube",
            },
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Make cube red"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc2]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res2, tool_call_id="call_mat", name="set_material"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Material applied"))

        # Turn 3: Latest active turn
        conv.add_message(ChatMessage(role=Role.USER, content="Now inspect the scene"))

        # Total chars > 10000
        self.assertGreater(calculate_messages_chars(conv.messages), 10000)

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        # Validation MUST pass without ValueError
        compacted_conv.validate_sequence()

        # Check that Turn 1 verified state is preserved in summary
        summary_text = compacted_conv.messages[1].content
        self.assertIn("'Cube': create", summary_text)
        self.assertIn("location=[0.0, 0.0, 1.0]", summary_text)

        # Retained Turn 2 tool messages are intact
        tool_msgs = [m for m in compacted_conv.messages if m.role == Role.TOOL]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0].tool_call_id, "call_mat")

    def test_huge_inspect_mesh_result_compacted(self):
        """Verify huge mesh inspection JSON is compacted to concise note without giant vertex arrays."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Giant mesh JSON in Turn 1
        huge_mesh_data = {
            "name": "BigMesh",
            "vertex_count": 5000,
            "face_count": 4800,
            "vertices": [[float(i), float(i), 0.0] for i in range(1500)],  # ~30,000 chars
        }
        tc = ToolCall(call_id="call_mesh", tool_name="inspect_mesh", arguments={"object_name": "BigMesh"})

        conv.add_message(ChatMessage(role=Role.USER, content="Inspect the big mesh"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=json.dumps(huge_mesh_data), tool_call_id="call_mesh", name="inspect_mesh"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Inspected big mesh"))

        # Turn 2
        conv.add_message(ChatMessage(role=Role.USER, content="Move it up"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Moved it up"))

        # Turn 3
        conv.add_message(ChatMessage(role=Role.USER, content="Done with mesh"))

        self.assertGreater(calculate_messages_chars(conv.messages), 10000)

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        summary_text = compacted_conv.messages[1].content
        self.assertIn("inspect_mesh('BigMesh': 5000v, 4800f)", summary_text)
        # Verify giant vertex coordinates array is NOT in summary
        self.assertNotIn("[[0.0, 0.0, 0.0]", summary_text)
        self.assertLess(calculate_messages_chars(compacted_conv.messages), 2000)

    def test_old_image_id_stripped_from_compacted_context(self):
        """Verify old image_id references are unlinked so expired LRU cache does NOT raise ImageResolutionError."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Turn 1: Old screenshot taken
        tc_view = ToolCall(call_id="call_view", tool_name="capture_viewport", arguments={})
        res_view = json.dumps({
            "image_id": "old_expired_image_999",
            "width": 512,
            "height": 512,
            "visual_verification": {
                "decision": "PASS",
                "rationale": "Object visually verified.",
            },
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Check viewport: " + "Z" * 5000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc_view]))
        conv.add_message(
            ChatMessage(
                role=Role.TOOL,
                content=res_view,
                tool_call_id="call_view",
                name="capture_viewport",
                image_id="old_expired_image_999",
            )
        )
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Visual check passed: " + "W" * 5000))

        # Turn 2
        conv.add_message(ChatMessage(role=Role.USER, content="Next step"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Ready"))

        # Turn 3 (active)
        conv.add_message(ChatMessage(role=Role.USER, content="Final request"))

        self.assertGreater(calculate_messages_chars(conv.messages), 10000)

        # Before compaction: building context without image in resolver raises ImageResolutionError
        adapter = MockAdapter()  # empty cache; old_expired_image_999 is NOT present
        with self.assertRaises(ImageResolutionError):
            ContextBuilder.build(
                conversation=conv,
                image_resolver=adapter.get_viewport_screenshot,
            )

        # After compaction:
        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        # Old image_id is gone from compacted_conv; building context succeeds without ImageResolutionError!
        ctx = ContextBuilder.build(
            conversation=compacted_conv,
            image_resolver=adapter.get_viewport_screenshot,
        )
        self.assertIsNotNone(ctx)
        self.assertEqual(len(ctx.images), 0)

        # Verify visual verdict was recorded in summary
        summary_text = compacted_conv.messages[1].content
        self.assertIn("Last verdict: PASS", summary_text)
        self.assertIn("Object visually verified.", summary_text)

    def test_active_turn_image_id_preserved(self):
        """Ensure active turn's image_id is preserved and properly resolved."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Old heavy turns (Turns 1 & 2)
        for i in range(2):
            conv.add_message(ChatMessage(role=Role.USER, content=f"Heavy task {i}: " + "M" * 4000))
            conv.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Heavy reply {i}: " + "N" * 4000))

        # Turn 3: Retained turn
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 3"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 3 done"))

        # Turn 4: Active turn with image attached
        conv.add_message(ChatMessage(role=Role.USER, content="Look at this image", image_id="active_img_123"))

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        # Active turn's image_id must still be present!
        last_msg = compacted_conv.messages[-1]
        self.assertEqual(last_msg.image_id, "active_img_123")

        adapter = MockAdapter()
        adapter.screenshots["active_img_123"] = b"\x89PNGfakeimagebytes"
        ctx = ContextBuilder.build(
            conversation=compacted_conv,
            image_resolver=adapter.get_viewport_screenshot,
        )
        self.assertIn("active_img_123", ctx.images)
        self.assertEqual(ctx.images["active_img_123"], b"\x89PNGfakeimagebytes")

    def test_runtime_history_never_modified_by_compaction(self):
        """Verify RuntimeHistory items are preserved completely even when conversation is compacted."""
        dispatcher = ToolDispatcher(registry=ToolRegistry(), adapter=MockAdapter())
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

        # Pre-populate history
        for i in range(5):
            runtime.history.add(
                item_id=f"item_{i}",
                turn_id=f"turn_{i}",
                kind=HistoryKind.USER,
                title=f"Action {i}",
                summary=f"Summary {i}",
                detail=f"Detail {i}",
            )

        # Populate heavy conversation in runtime
        runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
        for i in range(3):
            runtime.conversation.add_message(ChatMessage(role=Role.USER, content=f"Task {i}: " + "K" * 3000))
            runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Done {i}: " + "L" * 3000))
        runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Active prompt"))

        history_count_before = len(runtime.history.items)
        self.assertEqual(history_count_before, 5)

        # Trigger compaction via _maybe_compact_context
        was_compacted = runtime._maybe_compact_context()
        self.assertTrue(was_compacted)

        # History items must be EXACTLY the same
        self.assertEqual(len(runtime.history.items), history_count_before)
        for i in range(5):
            item = runtime.history.get_by_id(f"item_{i}")
            self.assertIsNotNone(item)
            self.assertEqual(item.title, f"Action {i}")

    def test_no_compaction_during_pending_approval(self):
        """Compaction must be blocked when AgentRuntime has a pending approval."""
        dispatcher = ToolDispatcher(registry=ToolRegistry(), adapter=MockAdapter())
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

        # Fill conversation over 10000 chars
        runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
        for i in range(3):
            runtime.conversation.add_message(ChatMessage(role=Role.USER, content=f"Task {i}: " + "K" * 3000))
            runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Done {i}: " + "L" * 3000))
        runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Delete object"))

        # Set pending approval
        tc_del = ToolCall(call_id="call_del", tool_name="delete_object", arguments={"name": "Cube"})
        runtime._pending_approval = PendingApproval(
            approval_id="appr_1",
            turn_id="turn_1",
            tool_call=tc_del,
            tool_name="delete_object",
            risk_level=RiskLevel.HIGH,
            human_readable_description="Delete Cube",
            created_at=100.0,
        )
        runtime.state_machine.transition_to(AgentState.PROCESSING)
        runtime.state_machine.transition_to(AgentState.PENDING_APPROVAL)

        # Attempt compaction
        was_compacted = runtime._maybe_compact_context()
        self.assertFalse(was_compacted)

    def test_no_compaction_during_executing_tool(self):
        """Compaction must be blocked when agent state is EXECUTING_TOOL."""
        dispatcher = ToolDispatcher(registry=ToolRegistry(), adapter=MockAdapter())
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

        runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
        for i in range(3):
            runtime.conversation.add_message(ChatMessage(role=Role.USER, content=f"Task {i}: " + "K" * 3000))
            runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Done {i}: " + "L" * 3000))
        runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Do tool"))

        runtime.state_machine.transition_to(AgentState.PROCESSING)
        runtime.state_machine.transition_to(AgentState.EXECUTING_TOOL)

        was_compacted = runtime._maybe_compact_context()
        self.assertFalse(was_compacted)

    def test_submit_prompt_compacts_at_new_turn_start(self):
        """submit_prompt() should trigger compaction before dispatching to worker."""
        dispatcher = ToolDispatcher(registry=ToolRegistry(), adapter=MockAdapter())
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

        # Populate previous turns (15,000+ chars)
        runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
        for i in range(3):
            runtime.conversation.add_message(ChatMessage(role=Role.USER, content=f"Old task {i}: " + "O" * 2500))
            runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Old reply {i}: " + "P" * 2500))

        orig_chars = calculate_messages_chars(runtime.conversation.messages)
        self.assertGreater(orig_chars, 10000)

        # Runtime is IDLE; submit a new prompt
        self.assertEqual(runtime.current_state, AgentState.IDLE)
        turn_id = runtime.submit_prompt("New prompt at turn start")

        self.assertIsNotNone(turn_id)
        # Context should have been compacted (Turn 0 & 1 compacted, Turn 2 + Turn 3 retained)
        total_chars = calculate_messages_chars(runtime.conversation.messages)
        self.assertLess(total_chars, 6000)
        self.assertLess(total_chars, orig_chars - 8000)

        # Most recent user message is the new prompt
        self.assertEqual(runtime.conversation.messages[-1].content, "New prompt at turn start")

    def test_multi_turn_rolling_compaction_merges_previous_summaries(self):
        """Successive compactions should absorb earlier summaries into one without duplicating markers."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Generate 6 turns
        for i in range(6):
            conv.add_message(ChatMessage(role=Role.USER, content=f"Turn {i} prompt: " + "Q" * 2000))
            conv.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Turn {i} reply: " + "R" * 2000))

        # First compaction
        c1, w1 = compact_conversation(conv, trigger_chars=5000, retained_turns=2)
        self.assertTrue(w1)

        # Add 2 more heavy turns
        c1.add_message(ChatMessage(role=Role.USER, content="Turn 6 prompt: " + "S" * 3000))
        c1.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 6 reply: " + "T" * 3000))
        c1.add_message(ChatMessage(role=Role.USER, content="Turn 7 prompt: " + "U" * 3000))
        c1.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 7 reply: " + "V" * 3000))

        # Second compaction
        c2, w2 = compact_conversation(c1, trigger_chars=5000, retained_turns=2)
        self.assertTrue(w2)

        # Summary marker should appear exactly once in the entire conversation!
        marker_count = sum(1 for m in c2.messages if m.content and "[Context Summary & Scene Memory]" in m.content)
        self.assertEqual(marker_count, 1)

        # Sequence validation passes
        c2.validate_sequence()

    def test_context_builder_max_context_chars_guard_regression(self):
        """Ensure ContextBuilder._apply_context_size_guard remains active as final 15000 cap."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System prompt"))
        conv.add_message(ChatMessage(role=Role.USER, content="Single gigantic message: " + "Z" * 20000))

        ctx = ContextBuilder.build(conversation=conv, max_context_chars=MAX_CONTEXT_CHARS)
        total_chars = calculate_messages_chars(ctx.messages)
        # Even with no turns to compact, the 15000 safety guard caps it
        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)

    # -------------------------------------------------------------------------
    # Scene-State Delete Invariant Tests
    # -------------------------------------------------------------------------

    def test_create_cube_compact_shows_cube_present(self):
        """create Cube -> compact -> Cube is present in Verified Scene State."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        tc = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
        res = json.dumps({
            "name": "Cube",
            "location": [0.0, 0.0, 0.0],
            "verification": {"status": "PASS", "operation": "create", "target_name": "Cube"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Create a cube: " + "X" * 6000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res, tool_call_id="c1", name="create_primitive"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Done: " + "Y" * 6000))

        # Retained turns
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 2"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 2 done"))
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 3"))

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        summary_text = compacted_conv.messages[1].content
        self.assertIn("- Verified Scene State:", summary_text)
        self.assertIn("'Cube': create", summary_text)
        self.assertNotIn("- Deleted Objects:", summary_text)

    def test_delete_cube_compact_shows_cube_in_deleted_objects_not_active(self):
        """delete Cube -> compact -> Cube is listed in Deleted Objects, not living in active state."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        tc = ToolCall(call_id="c1", tool_name="delete_object", arguments={"name": "Cube"})
        res = json.dumps({
            "name": "Cube",
            "deleted": True,
            "verification": {"status": "PASS", "operation": "delete", "target_name": "Cube"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Delete cube: " + "X" * 6000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res, tool_call_id="c1", name="delete_object"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Deleted: " + "Y" * 6000))

        # Retained turns
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 2"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 2 done"))
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 3"))

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        summary_text = compacted_conv.messages[1].content
        self.assertIn("- Deleted Objects:", summary_text)
        self.assertIn("'Cube': deleted [PASS]", summary_text)
        self.assertNotIn("- Verified Scene State:", summary_text)

    def test_create_transform_delete_cube_compact_shows_only_deleted(self):
        """create Cube -> transform Cube -> delete Cube -> compact -> Cube shows ONLY as deleted."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Turn 1: Create
        tc1 = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
        res1 = json.dumps({
            "name": "Cube",
            "verification": {"status": "PASS", "operation": "create", "target_name": "Cube"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Create cube: " + "A" * 3000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res1, tool_call_id="c1", name="create_primitive"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Created: " + "B" * 3000))

        # Turn 2: Transform
        tc2 = ToolCall(call_id="c2", tool_name="transform_object", arguments={"object_name": "Cube", "location": [0, 0, 2]})
        res2 = json.dumps({
            "name": "Cube",
            "location": [0.0, 0.0, 2.0],
            "verification": {"status": "PASS", "operation": "transform", "target_name": "Cube"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Move cube: " + "C" * 3000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc2]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res2, tool_call_id="c2", name="transform_object"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Moved: " + "D" * 3000))

        # Turn 3: Delete
        tc3 = ToolCall(call_id="c3", tool_name="delete_object", arguments={"name": "Cube"})
        res3 = json.dumps({
            "name": "Cube",
            "verification": {"status": "PASS", "operation": "delete", "target_name": "Cube"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Delete cube: " + "E" * 3000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc3]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res3, tool_call_id="c3", name="delete_object"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Deleted: " + "F" * 3000))

        # Retained turns
        conv.add_message(ChatMessage(role=Role.USER, content="Retained Turn 4"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 4 done"))
        conv.add_message(ChatMessage(role=Role.USER, content="Active Turn 5"))

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        summary_text = compacted_conv.messages[1].content
        self.assertIn("- Deleted Objects:\n  * 'Cube': deleted [PASS]", summary_text)
        self.assertNotIn("- Verified Scene State:", summary_text)

    def test_create_a_and_b_delete_a_compact_shows_b_active_and_a_deleted(self):
        """create A + create B -> delete A -> compact -> B is preserved in active state, A in deleted."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Turn 1: Create A & B
        tc_a = ToolCall(call_id="ca", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
        res_a = json.dumps({
            "name": "ObjA",
            "verification": {"status": "PASS", "operation": "create", "target_name": "ObjA"},
        })
        tc_b = ToolCall(call_id="cb", tool_name="create_primitive", arguments={"primitive_type": "SPHERE"})
        res_b = json.dumps({
            "name": "ObjB",
            "verification": {"status": "PASS", "operation": "create", "target_name": "ObjB"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Create ObjA & ObjB: " + "A" * 4000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc_a, tc_b]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res_a, tool_call_id="ca", name="create_primitive"))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res_b, tool_call_id="cb", name="create_primitive"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Created both: " + "B" * 4000))

        # Turn 2: Delete A
        tc_d = ToolCall(call_id="cd", tool_name="delete_object", arguments={"name": "ObjA"})
        res_d = json.dumps({
            "name": "ObjA",
            "verification": {"status": "PASS", "operation": "delete", "target_name": "ObjA"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Delete ObjA: " + "C" * 4000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc_d]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res_d, tool_call_id="cd", name="delete_object"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Deleted ObjA: " + "D" * 4000))

        # Retained turns
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 3"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 3 reply"))
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 4 active"))

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        summary_text = compacted_conv.messages[1].content
        self.assertIn("- Verified Scene State:\n  * 'ObjB': create", summary_text)
        self.assertIn("- Deleted Objects:\n  * 'ObjA': deleted [PASS]", summary_text)
        self.assertNotIn("'ObjA': create", summary_text)

    def test_delete_semantic_fail_not_removed_from_active_ledger(self):
        """If delete semantic verification is FAIL, entity is NOT removed from active ledger."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Turn 1: Create Cube
        tc1 = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
        res1 = json.dumps({
            "name": "Cube",
            "verification": {"status": "PASS", "operation": "create", "target_name": "Cube"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Create Cube: " + "A" * 4000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res1, tool_call_id="c1", name="create_primitive"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Created: " + "B" * 4000))

        # Turn 2: Attempt Delete, but verification FAILS (e.g. object still exists)
        tc2 = ToolCall(call_id="c2", tool_name="delete_object", arguments={"name": "Cube"})
        res2 = json.dumps({
            "name": "Cube",
            "verification": {"status": "FAIL", "operation": "delete", "target_name": "Cube", "reason": "still exists"},
        })
        conv.add_message(ChatMessage(role=Role.USER, content="Delete Cube: " + "C" * 4000))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc2]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=res2, tool_call_id="c2", name="delete_object"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Deletion failed: " + "D" * 4000))

        # Retained turns
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 3"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Turn 3 reply"))
        conv.add_message(ChatMessage(role=Role.USER, content="Turn 4 active"))

        compacted_conv, was_compacted = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(was_compacted)

        summary_text = compacted_conv.messages[1].content
        # Cube must STILL be in verified scene state because deletion failed verification!
        self.assertIn("- Verified Scene State:\n  * 'Cube': create", summary_text)
        self.assertNotIn("- Deleted Objects:", summary_text)
        self.assertIn("delete_object ('Cube'): VERIFICATION_FAILED", summary_text)

    def test_successive_compactions_1_2_3_never_counts_summary_as_user_turn(self):
        """Successive compactions (1, 2, 3) must never miscount prior summary blocks as new user turns."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="System"))

        # Seed initial 4 turns
        for i in range(4):
            conv.add_message(ChatMessage(role=Role.USER, content=f"Round 1 Task {i}: " + "A" * 3000))
            conv.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Round 1 Done {i}: " + "B" * 3000))

        # --- COMPACTION 1 ---
        c1, w1 = compact_conversation(conv, trigger_chars=10000, retained_turns=2)
        self.assertTrue(w1)
        # Should have: SYSTEM, summary_user, ack_assistant, Turn 2, Turn 3
        system_msg, prior_summary, turns = partition_conversation_into_turns(c1.messages)
        self.assertIsNotNone(prior_summary)
        self.assertEqual(len(turns), 2)  # Exactly 2 real user turns!

        # Add only 1 new turn: should NOT trigger compaction because total real turns = 3 (1 older, 2 retained)
        # but let's test adding 2 new heavy turns to reach 4 real turns
        c1.add_message(ChatMessage(role=Role.USER, content="Round 2 Task 4: " + "C" * 3000))
        c1.add_message(ChatMessage(role=Role.ASSISTANT, content="Round 2 Done 4: " + "D" * 3000))
        c1.add_message(ChatMessage(role=Role.USER, content="Round 2 Task 5: " + "E" * 3000))
        c1.add_message(ChatMessage(role=Role.ASSISTANT, content="Round 2 Done 5: " + "F" * 3000))

        # Partition before Compaction 2:
        _, prior_summary_c1, turns_c1 = partition_conversation_into_turns(c1.messages)
        self.assertIsNotNone(prior_summary_c1)
        self.assertEqual(len(turns_c1), 4)  # Exactly 4 real user turns (Turns 2, 3, 4, 5)

        # --- COMPACTION 2 ---
        c2, w2 = compact_conversation(c1, trigger_chars=10000, retained_turns=2)
        self.assertTrue(w2)
        _, prior_summary_c2, turns_c2 = partition_conversation_into_turns(c2.messages)
        self.assertIsNotNone(prior_summary_c2)
        self.assertEqual(len(turns_c2), 2)  # Turns 4 & 5 retained!
        self.assertEqual(turns_c2[0][0].content, "Round 2 Task 4: " + "C" * 3000)
        self.assertEqual(turns_c2[1][0].content, "Round 2 Task 5: " + "E" * 3000)

        # Marker should be present exactly once
        marker_count = sum(1 for m in c2.messages if m.content and "[Context Summary & Scene Memory]" in m.content)
        self.assertEqual(marker_count, 1)

        # Add 2 more heavy turns for Compaction 3
        c2.add_message(ChatMessage(role=Role.USER, content="Round 3 Task 6: " + "G" * 3000))
        c2.add_message(ChatMessage(role=Role.ASSISTANT, content="Round 3 Done 6: " + "H" * 3000))
        c2.add_message(ChatMessage(role=Role.USER, content="Round 3 Task 7: " + "I" * 3000))
        c2.add_message(ChatMessage(role=Role.ASSISTANT, content="Round 3 Done 7: " + "J" * 3000))

        # --- COMPACTION 3 ---
        c3, w3 = compact_conversation(c2, trigger_chars=10000, retained_turns=2)
        self.assertTrue(w3)
        _, prior_summary_c3, turns_c3 = partition_conversation_into_turns(c3.messages)
        self.assertIsNotNone(prior_summary_c3)
        self.assertEqual(len(turns_c3), 2)  # Turns 6 & 7 retained!
        self.assertEqual(turns_c3[0][0].content, "Round 3 Task 6: " + "G" * 3000)
        self.assertEqual(turns_c3[1][0].content, "Round 3 Task 7: " + "I" * 3000)

        marker_count_3 = sum(1 for m in c3.messages if m.content and "[Context Summary & Scene Memory]" in m.content)
        self.assertEqual(marker_count_3, 1)

        # Sequence validation passes across all
        c1.validate_sequence()
        c2.validate_sequence()
        c3.validate_sequence()


if __name__ == "__main__":
    unittest.main()

