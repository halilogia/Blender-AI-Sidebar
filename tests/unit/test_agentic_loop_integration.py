"""Unit and integration tests for M9 Task 2: Agentic Loop Integration & Multi-Round Flow.

Covers the full agentic execution loop matrix:
- Test A: Successful multi-round plan (propose_plan -> approve -> execute -> tool feedback -> second LLM call -> final response)
- Test B: Failed plan -> repair plan -> success (targeted repair, no duplicate executions of prior completed steps, approval enforced, final response reached)
- Test C: Failed plan -> failed repair -> hard stop (MAX_PLAN_REPAIRS_EXCEEDED termination guard)
- Test D: User rejects plan -> USER_REJECTED tool feedback -> LLM resumption
- Test E: Single-tool execution regression (mutation -> approval -> execute -> verify)
- Test F: Invalid plan proposal handling (PLAN_VALIDATION_FAILED -> graceful error -> valid conversation sequence)

Strictly ZERO Blender (bpy) dependencies. Pure Python standard library only.
"""

import json
import unittest
from unittest.mock import MagicMock

from core.events import (
    AgentErrorEvent,
    ApprovalRequiredEvent,
    ApprovalResolvedEvent,
    FinalResponseReadyEvent,
    ProviderResponseReadyEvent,
    ToolResultReadyEvent,
)
from core.types import RiskLevel, ToolResult
from agent.dispatcher import ToolDispatcher
from agent.models import (
    ChatMessage,
    Conversation,
    ProviderResponse,
    Role,
    ToolCall,
)
from agent.policy import ApprovalDecision, ApprovalPolicy, InvalidApprovalError, NoPendingApprovalError
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from tools.base import BaseTool
from tools.registry import ToolRegistry
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.delete_object import DeleteObjectTool
from tools.mutations.transform_object import TransformObjectTool


def _create_test_plan(title="Test Plan"):
    return {
        "title": title,
        "description": "Multi-step plan for testing",
        "steps": [
            {
                "step_id": "s1",
                "tool_name": "create_primitive",
                "arguments": {"primitive_type": "CUBE"},
                "description": "Create a cube",
            },
            {
                "step_id": "s2",
                "tool_name": "transform_object",
                "arguments": {"name": "Cube", "location": [0.0, 0.0, 2.0]},
                "description": "Move the cube up",
                "depends_on": ["s1"],
            },
        ],
    }


def _create_repair_plan(title="Repair Plan"):
    """Targeted repair plan containing only the remaining/failed step."""
    return {
        "title": title,
        "description": "Targeted repair step only",
        "steps": [
            {
                "step_id": "s2_repair",
                "tool_name": "transform_object",
                "arguments": {"name": "Cube", "location": [0.0, 0.0, 1.0]},
                "description": "Retry moving the cube",
            },
        ],
    }


def _setup_runtime():
    reg = ToolRegistry()
    reg.register(CreatePrimitiveTool())
    reg.register(DeleteObjectTool())
    reg.register(TransformObjectTool())

    adapter = MagicMock()
    adapter.create_primitive.return_value = ToolResult.ok(tool="create_primitive", data={"name": "Cube"})
    adapter.delete_object.return_value = ToolResult.ok(tool="delete_object", data={"deleted": True})
    adapter.transform_object.return_value = ToolResult.ok(tool="transform_object", data={"ok": True})

    dispatcher = ToolDispatcher(registry=reg, adapter=adapter)
    provider = MagicMock()
    provider.last_tool_calls = []

    runtime = AgentRuntime(
        provider=provider,
        dispatcher=dispatcher,
        policy=ApprovalPolicy(),
        verifier=False,
        visual_verifier=MagicMock(),
        max_plan_repairs=1,
        max_tool_rounds=5,
    )
    mock_worker = MagicMock()
    runtime.worker = mock_worker

    return runtime, adapter, mock_worker


class TestAgenticLoopIntegration(unittest.TestCase):
    """Full integration test suite for multi-round agentic plan & repair loops."""

    def test_a_successful_multi_round_plan(self):
        """Test A: User prompt -> propose_plan -> approve -> plan COMPLETED
        -> TOOL feedback -> second LLM response -> final answer.
        """
        runtime, adapter, mock_worker = _setup_runtime()

        # Step 1: User submits prompt
        turn_id = runtime.submit_prompt("Create a cube and move it")
        self.assertEqual(runtime.current_state, AgentState.PROCESSING)
        self.assertEqual(len(runtime.conversation.messages), 1)
        self.assertEqual(runtime.conversation.messages[0].role, Role.USER)

        # Step 2: LLM Round 1 responds with propose_plan tool call
        plan_data = _create_test_plan("Build Scene")
        resp1 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="call_p1", tool_name="propose_plan", arguments=plan_data)],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp1, turn_id=turn_id))

        # Verification of Round 1
        self.assertEqual(runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertIsNotNone(runtime.pending_plan_review)
        self.assertEqual(runtime.pending_plan_review.call_id, "call_p1")
        aid = runtime.pending_plan_review.approval_id

        # Assistant message with tool call recorded
        self.assertEqual(len(runtime.conversation.messages), 2)
        asst_msg = runtime.conversation.messages[1]
        self.assertEqual(asst_msg.role, Role.ASSISTANT)
        self.assertEqual(asst_msg.tool_calls[0].call_id, "call_p1")

        # Step 3: User approves plan
        summary = runtime.approve_plan(aid)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.status.value, "COMPLETED")
        self.assertEqual(adapter.create_primitive.call_count, 1)
        self.assertEqual(adapter.transform_object.call_count, 1)

        # Verification of tool feedback message
        self.assertEqual(runtime.current_state, AgentState.PROCESSING)
        self.assertEqual(len(runtime.conversation.messages), 3)
        tool_msg = runtime.conversation.messages[2]
        self.assertEqual(tool_msg.role, Role.TOOL)
        self.assertEqual(tool_msg.tool_call_id, "call_p1")
        self.assertEqual(tool_msg.name, "propose_plan")

        tool_content = json.loads(tool_msg.content)
        self.assertEqual(tool_content["status"], "COMPLETED")
        self.assertEqual(tool_content["steps_completed"], 2)

        # Worker submitted for round 2 (total 2 worker tasks in turn: initial prompt + post-approval)
        self.assertEqual(mock_worker.submit_task.call_count, 2)
        submitted_task_args = mock_worker.submit_task.call_args[1]
        self.assertEqual(submitted_task_args["turn_id"], turn_id)

        # Conversation sequence is valid at this point
        runtime.conversation.validate_sequence()

        # Duplicate execution prohibited
        with self.assertRaises((NoPendingApprovalError, InvalidApprovalError)):
            runtime.approve_plan(aid)

        # Step 4: LLM Round 2 responds with final text
        resp2 = ProviderResponse(
            assistant_text="Küp başarıyla oluşturuldu ve yukarı taşındı.",
            tool_calls=[],
            is_final=True,
        )
        result = runtime.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=turn_id))

        # Final verification
        self.assertIsNotNone(result)
        self.assertEqual(result.state, AgentState.IDLE.value)
        self.assertEqual(runtime.current_state, AgentState.IDLE)
        self.assertEqual(result.final_text, "Küp başarıyla oluşturuldu ve yukarı taşındı.")

        # Exactly 4 messages: User, Assistant(tool_call), Tool, Assistant(final)
        self.assertEqual(len(runtime.conversation.messages), 4)
        runtime.conversation.validate_sequence()

        # FinalResponseReadyEvent emitted
        queued = runtime.event_queue.drain_batch(max_items=50, max_time_sec=0.05)
        final_events = [e for e in queued if isinstance(e, FinalResponseReadyEvent)]
        self.assertEqual(len(final_events), 1)

    def test_b_failed_plan_repair_and_success(self):
        """Test B: Initial plan -> approve -> plan FAILED -> TOOL failure feedback
        -> LLM repair plan -> approve -> repair COMPLETED -> TOOL success feedback
        -> LLM final.
        """
        runtime, adapter, mock_worker = _setup_runtime()

        # Step 1: Configure adapter so step s2 fails
        adapter.transform_object.return_value = ToolResult.fail(
            tool="transform_object",
            error_type="BLENDER_FAIL",
            message="Transform axis locked",
        )

        turn_id = runtime.submit_prompt("Build and position cube")

        # Step 2: LLM proposes initial plan
        resp1 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="call_init", tool_name="propose_plan", arguments=_create_test_plan())],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp1, turn_id=turn_id))
        aid1 = runtime.pending_plan_review.approval_id

        # Step 3: Approve initial plan -> fails at s2
        summary1 = runtime.approve_plan(aid1)
        self.assertEqual(summary1.status.value, "FAILED")
        self.assertEqual(adapter.create_primitive.call_count, 1)
        self.assertEqual(adapter.transform_object.call_count, 1)

        # Tool failure feedback in conversation
        tool_msg1 = runtime.conversation.last()
        self.assertEqual(tool_msg1.role, Role.TOOL)
        self.assertEqual(tool_msg1.tool_call_id, "call_init")
        fail_payload = json.loads(tool_msg1.content)
        self.assertEqual(fail_payload["status"], "FAILED")
        self.assertIn("Transform axis locked", fail_payload["failure_reason"])

        # Repair counter is still 0 (no repair proposed yet)
        self.assertEqual(runtime._current_plan_repairs, 0)
        runtime.conversation.validate_sequence()

        # Step 4: LLM inspects failure and proposes targeted repair plan (only step s2_repair)
        resp2 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="call_repair", tool_name="propose_plan", arguments=_create_repair_plan())],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=turn_id))

        # Verification of repair plan review & counter
        self.assertEqual(runtime._current_plan_repairs, 1)
        self.assertEqual(runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertIsNotNone(runtime.pending_plan_review)
        self.assertEqual(runtime.pending_plan_review.call_id, "call_repair")
        aid2 = runtime.pending_plan_review.approval_id

        # Step 5: Fix adapter transform failure for repair execution
        adapter.transform_object.return_value = ToolResult.ok(
            tool="transform_object",
            data={"transformed": True},
        )

        # User approves repair plan
        summary2 = runtime.approve_plan(aid2)
        self.assertEqual(summary2.status.value, "COMPLETED")

        # Crucial check: Step s1 (create_primitive) was NOT re-run!
        self.assertEqual(adapter.create_primitive.call_count, 1)
        # Step s2 (transform_object) was run twice (1 fail, 1 repair success)
        self.assertEqual(adapter.transform_object.call_count, 2)

        # Tool success feedback in conversation
        tool_msg2 = runtime.conversation.last()
        self.assertEqual(tool_msg2.role, Role.TOOL)
        self.assertEqual(tool_msg2.tool_call_id, "call_repair")
        repair_payload = json.loads(tool_msg2.content)
        self.assertEqual(repair_payload["status"], "COMPLETED")

        runtime.conversation.validate_sequence()

        # Step 6: LLM produces final answer
        resp3 = ProviderResponse(
            assistant_text="Düzeltme planı uygulandı, sahne tamamlandı.",
            tool_calls=[],
            is_final=True,
        )
        final_res = runtime.process_event(ProviderResponseReadyEvent(response=resp3, turn_id=turn_id))

        self.assertIsNotNone(final_res)
        self.assertEqual(final_res.state, AgentState.IDLE.value)
        self.assertEqual(runtime.current_state, AgentState.IDLE)
        self.assertEqual(final_res.final_text, "Düzeltme planı uygulandı, sahne tamamlandı.")
        runtime.conversation.validate_sequence()

    def test_c_failed_plan_failed_repair_hard_stop(self):
        """Test C: Initial plan FAIL -> repair FAIL -> third repair proposal
        -> MAX_PLAN_REPAIRS_EXCEEDED -> no further execution.
        """
        runtime, adapter, _ = _setup_runtime()

        adapter.create_primitive.return_value = ToolResult.fail(
            tool="create_primitive",
            error_type="FAIL",
            message="Fatal",
        )

        turn_id = runtime.submit_prompt("Build scene")

        # 1. Initial plan fails
        resp1 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c1", tool_name="propose_plan", arguments=_create_test_plan())],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp1, turn_id=turn_id))
        aid1 = runtime.pending_plan_review.approval_id
        runtime.approve_plan(aid1)

        # 2. Repair plan 1 proposed & approved -> fails again
        resp2 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c2", tool_name="propose_plan", arguments=_create_repair_plan())],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=turn_id))
        self.assertEqual(runtime._current_plan_repairs, 1)

        aid2 = runtime.pending_plan_review.approval_id
        runtime.approve_plan(aid2)

        # 3. Third plan attempt (second repair proposal) -> hits max_plan_repairs=1
        resp3 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c3", tool_name="propose_plan", arguments=_create_repair_plan())],
            is_final=False,
        )
        err_res = runtime.process_event(ProviderResponseReadyEvent(response=resp3, turn_id=turn_id))

        self.assertIsNotNone(err_res)
        self.assertEqual(err_res.state, AgentState.ERROR.value)
        self.assertEqual(runtime.current_state, AgentState.ERROR)
        self.assertIn("Maximum plan repairs limit reached", err_res.final_text)

        # Active turn cleared
        self.assertIsNone(runtime.current_turn_id)
        # Conversation sequence integrity verified
        runtime.conversation.validate_sequence()
        # Last message is tool error response matching call_id c3
        last_msg = runtime.conversation.last()
        self.assertEqual(last_msg.role, Role.TOOL)
        self.assertEqual(last_msg.tool_call_id, "c3")
        self.assertIn("MAX_PLAN_REPAIRS_EXCEEDED", last_msg.content)

    def test_d_user_rejects_plan_feedback_and_llm_resumption(self):
        """Test D: propose_plan -> reject -> USER_REJECTED TOOL feedback -> LLM resumed."""
        runtime, adapter, mock_worker = _setup_runtime()

        turn_id = runtime.submit_prompt("Build something dangerous")

        # LLM proposes plan
        resp1 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="call_rej", tool_name="propose_plan", arguments=_create_test_plan())],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp1, turn_id=turn_id))
        aid = runtime.pending_plan_review.approval_id

        # User rejects plan
        runtime.reject_plan(aid)
        self.assertEqual(runtime.current_state, AgentState.PROCESSING)

        # Zero adapter mutations executed
        adapter.create_primitive.assert_not_called()
        adapter.transform_object.assert_not_called()

        # Tool message carrying USER_REJECTED recorded in conversation
        tool_msg = runtime.conversation.last()
        self.assertEqual(tool_msg.role, Role.TOOL)
        self.assertEqual(tool_msg.tool_call_id, "call_rej")
        self.assertEqual(tool_msg.name, "propose_plan")
        rej_data = json.loads(tool_msg.content)
        self.assertEqual(rej_data["status"], "USER_REJECTED")

        # Sequence integrity preserved
        runtime.conversation.validate_sequence()

        # Worker submitted for resumption (total 2 calls in turn: submit_prompt + reject_plan)
        self.assertEqual(mock_worker.submit_task.call_count, 2)

        # LLM responds politely acknowledging rejection
        resp2 = ProviderResponse(
            assistant_text="Anladım, plan iptal edildi. Nasıl bir değişiklik yapalım?",
            tool_calls=[],
            is_final=True,
        )
        res = runtime.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=turn_id))

        self.assertIsNotNone(res)
        self.assertEqual(res.state, AgentState.IDLE.value)
        self.assertEqual(runtime.current_state, AgentState.IDLE)
        runtime.conversation.validate_sequence()

    def test_e_single_tool_approval_regression(self):
        """Test E: Single mutation -> approval -> execute -> verify (M9 does not break single-tool path)."""
        runtime, adapter, mock_worker = _setup_runtime()

        turn_id = runtime.submit_prompt("Delete the Cube")

        # LLM proposes single tool call
        tc = ToolCall(call_id="tc_del", tool_name="delete_object", arguments={"name": "Cube"})
        resp1 = ProviderResponse(
            assistant_text=None,
            tool_calls=[tc],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp1, turn_id=turn_id))

        # Policy requires approval for delete_object (MEDIUM risk)
        self.assertEqual(runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertIsNotNone(runtime.pending_approval)
        self.assertEqual(runtime.pending_approval.tool_name, "delete_object")
        aid = runtime.pending_approval.approval_id

        # User approves single tool
        runtime.approve(aid)

        # Single mutation executed
        self.assertEqual(adapter.delete_object.call_count, 1)
        self.assertEqual(runtime.current_state, AgentState.PROCESSING)

        # Tool result message appended
        tool_msg = runtime.conversation.last()
        self.assertEqual(tool_msg.role, Role.TOOL)
        self.assertEqual(tool_msg.tool_call_id, "tc_del")
        self.assertEqual(tool_msg.name, "delete_object")
        runtime.conversation.validate_sequence()

        # LLM provides final answer
        resp2 = ProviderResponse(
            assistant_text="Küp başarıyla silindi.",
            tool_calls=[],
            is_final=True,
        )
        final_res = runtime.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=turn_id))
        self.assertIsNotNone(final_res)
        self.assertEqual(final_res.state, AgentState.IDLE.value)
        self.assertEqual(runtime.current_state, AgentState.IDLE)
        runtime.conversation.validate_sequence()

    def test_f_invalid_plan_proposal_terminates_gracefully(self):
        """Test F: Invalid plan structure terminates cleanly without hanging or breaking conversation sequence."""
        runtime, _, _ = _setup_runtime()

        turn_id = runtime.submit_prompt("Create bad plan")

        # LLM proposes invalid plan (empty steps)
        bad_plan = {"title": "Bad Plan", "steps": []}
        resp = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="call_bad", tool_name="propose_plan", arguments=bad_plan)],
            is_final=False,
        )
        err_res = runtime.process_event(ProviderResponseReadyEvent(response=resp, turn_id=turn_id))

        self.assertIsNotNone(err_res)
        self.assertEqual(err_res.state, AgentState.ERROR.value)
        self.assertEqual(runtime.current_state, AgentState.ERROR)
        self.assertIn("Plan validation failed", err_res.final_text)
        self.assertEqual(err_res.tool_results[0].error.type, "PLAN_VALIDATION_FAILED")

        # Last tool message carries PLAN_VALIDATION_FAILED for call_bad
        last_msg = runtime.conversation.last()
        self.assertEqual(last_msg.role, Role.TOOL)
        self.assertEqual(last_msg.tool_call_id, "call_bad")
        self.assertIn("PLAN_VALIDATION_FAILED", last_msg.content)

    def test_g_max_tool_rounds_limits_multi_round_loop(self):
        """Test G: max_tool_rounds strictly bounds agentic loop rounds."""
        runtime, _, _ = _setup_runtime()
        runtime.max_tool_rounds = 2  # allow only 2 tool rounds

        turn_id = runtime.submit_prompt("Test round limit")

        # Round 1: propose plan
        resp1 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="r1", tool_name="propose_plan", arguments=_create_test_plan())],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp1, turn_id=turn_id))
        aid1 = runtime.pending_plan_review.approval_id
        runtime.approve_plan(aid1)
        self.assertEqual(runtime._current_tool_round, 1)

        # Round 2: LLM asks for another tool call (round 2 allowed)
        resp2 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="r2", tool_name="create_primitive", arguments={"primitive_type": "SPHERE"})],
            is_final=False,
        )
        runtime.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=turn_id))
        self.assertEqual(runtime._current_tool_round, 2)

        # Round 3: LLM asks for third tool call -> trips max_tool_rounds=2
        resp3 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="r3", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})],
            is_final=False,
        )
        err = runtime.process_event(ProviderResponseReadyEvent(response=resp3, turn_id=turn_id))
        self.assertIsNotNone(err)
        self.assertEqual(err.state, AgentState.ERROR.value)
        self.assertEqual(runtime.current_state, AgentState.ERROR)
        self.assertIn("Maximum tool rounds limit reached", err.final_text)

    def test_h_stale_turn_event_dropped_in_multi_round(self):
        """Test H: Stale turn event with old turn_id is safely dropped and does not mutate active turn."""
        runtime, _, _ = _setup_runtime()

        turn_id = runtime.submit_prompt("Active turn")

        # Stale event with previous turn_id arrives
        stale_resp = ProviderResponse(assistant_text="From past turn", is_final=True)
        result = runtime.process_event(ProviderResponseReadyEvent(response=stale_resp, turn_id="old_turn_99"))

        # Event dropped
        self.assertIsNone(result)
        self.assertEqual(runtime._stale_events_count, 1)
        self.assertEqual(runtime.current_turn_id, turn_id)
        self.assertEqual(runtime.current_state, AgentState.PROCESSING)


if __name__ == "__main__":
    unittest.main()
