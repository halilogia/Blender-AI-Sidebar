"""Unit tests for ContextBuilder and ProviderRequestContext (M2.3.3 - M2.3.6).

Zero Blender dependencies. Pure Python.
"""

import json
import unittest

from agent.context_builder import (
    ContextBuilder,
    DEFAULT_SYSTEM_PROMPT,
    MAX_CONTEXT_CHARS,
    ProviderRequestContext,
)
from agent.models import ChatMessage, Conversation, Role, ToolCall
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_scene import InspectSceneTool


class TestContextBuilder(unittest.TestCase):
    """Test suite covering all ContextBuilder requirements and test matrix."""

    def test_empty_conversation_builds_default_system_prompt(self):
        conv = Conversation()
        ctx = ContextBuilder.build(conversation=conv)

        self.assertEqual(len(ctx.messages), 1)
        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)
        self.assertEqual(ctx.messages[0].content, DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(len(ctx.tools), 0)

    def test_system_and_user_conversation(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Sahneyi incele"))
        ctx = ContextBuilder.build(conversation=conv)

        self.assertEqual(len(ctx.messages), 2)
        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)
        self.assertEqual(ctx.messages[0].content, DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(ctx.messages[1].role, Role.USER)
        self.assertEqual(ctx.messages[1].content, "Sahneyi incele")

    def test_explicit_system_prompt_override(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Hello"))
        custom_prompt = "Custom system prompt for testing."
        ctx = ContextBuilder.build(conversation=conv, system_prompt=custom_prompt)

        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)
        self.assertEqual(ctx.messages[0].content, custom_prompt)
        self.assertEqual(ctx.system_prompt, custom_prompt)

    def test_assistant_and_tool_call_assembly(self):
        conv = Conversation()
        tc = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        conv.add_message(ChatMessage(role=Role.USER, content="İncele"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="İnceleniyor...", tool_calls=[tc]))

        ctx = ContextBuilder.build(conversation=conv)
        self.assertEqual(len(ctx.messages), 3)
        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)
        self.assertEqual(ctx.messages[1].role, Role.USER)
        self.assertEqual(ctx.messages[2].role, Role.ASSISTANT)
        self.assertEqual(ctx.messages[2].tool_calls[0].call_id, "call_1")

    def test_multi_tool_results_round_trip(self):
        conv = Conversation()
        tc1 = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        tc2 = ToolCall(call_id="call_2", tool_name="inspect_object", arguments={"name": "Cube"})

        conv.add_message(ChatMessage(role=Role.USER, content="Sahne ve küpü incele"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1, tc2]))
        conv.add_message(ChatMessage(role=Role.TOOL, content='{"scene": "Main"}', tool_call_id="call_1", name="inspect_scene"))
        conv.add_message(ChatMessage(role=Role.TOOL, content='{"name": "Cube"}', tool_call_id="call_2", name="inspect_object"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="İnceleme tamamlandı."))

        ctx = ContextBuilder.build(conversation=conv)
        self.assertEqual(len(ctx.messages), 6)  # System + 5 conversation messages
        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)
        self.assertEqual(ctx.messages[1].role, Role.USER)
        self.assertEqual(ctx.messages[2].role, Role.ASSISTANT)
        self.assertEqual(ctx.messages[3].role, Role.TOOL)
        self.assertEqual(ctx.messages[4].role, Role.TOOL)
        self.assertEqual(ctx.messages[5].role, Role.ASSISTANT)

    def test_message_ordering_is_strictly_preserved(self):
        conv = Conversation()
        for i in range(10):
            role = Role.USER if i % 2 == 0 else Role.ASSISTANT
            conv.add_message(ChatMessage(role=role, content=f"Message {i}"))

        ctx = ContextBuilder.build(conversation=conv)
        # System + 10 messages
        self.assertEqual(len(ctx.messages), 11)
        for i in range(10):
            expected_role = Role.USER if i % 2 == 0 else Role.ASSISTANT
            self.assertEqual(ctx.messages[i + 1].role, expected_role)
            self.assertEqual(ctx.messages[i + 1].content, f"Message {i}")

    def test_unicode_and_turkish_characters_preserved(self):
        turkish_text = "Çalışma masasındaki küpü, ışığı ve özel materyalleri incele: ğüşiöç / 🤖✨"
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content=turkish_text))

        ctx = ContextBuilder.build(conversation=conv)
        user_msg = ctx.messages[1]
        self.assertEqual(user_msg.content, turkish_text)

        # Check serialization preserves unicode
        d = ctx.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        self.assertIn("Çalışma masasındaki", json_str)
        self.assertIn("ğüşiöç", json_str)
        self.assertIn("🤖✨", json_str)

    def test_context_under_limit_no_eviction(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Kısa bir mesaj"))
        ctx = ContextBuilder.build(conversation=conv, max_context_chars=15000)

        self.assertEqual(len(ctx.messages), 2)
        self.assertEqual(ctx.messages[1].content, "Kısa bir mesaj")

    def test_context_over_limit_evicts_old_messages_first(self):
        conv = Conversation()
        # Old turn 1 (long content)
        conv.add_message(ChatMessage(role=Role.USER, content="Eski Soru 1: " + "A" * 300))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Eski Cevap 1: " + "B" * 300))
        # Old turn 2
        conv.add_message(ChatMessage(role=Role.USER, content="Eski Soru 2: " + "C" * 300))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Eski Cevap 2: " + "D" * 300))
        # Latest turn
        conv.add_message(ChatMessage(role=Role.USER, content="Yeni Soru: Küp nerede?"))

        # Set limit tight enough that old messages must be evicted
        ctx = ContextBuilder.build(conversation=conv, max_context_chars=800)

        contents = [m.content for m in ctx.messages]
        # System prompt must be retained
        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)
        # Latest user message must be retained
        self.assertEqual(ctx.messages[-1].content, "Yeni Soru: Küp nerede?")
        # Oldest message should be evicted
        self.assertFalse(any("Eski Soru 1" in (c or "") for c in contents))

    def test_system_and_latest_user_always_retained(self):
        conv = Conversation()
        for i in range(5):
            conv.add_message(ChatMessage(role=Role.USER, content=f"User {i}: " + "X" * 100))
            conv.add_message(ChatMessage(role=Role.ASSISTANT, content=f"Assistant {i}: " + "Y" * 100))
        conv.add_message(ChatMessage(role=Role.USER, content="Final active prompt."))

        ctx = ContextBuilder.build(conversation=conv, max_context_chars=500)

        self.assertEqual(ctx.messages[0].role, Role.SYSTEM)
        self.assertEqual(ctx.messages[-1].content, "Final active prompt.")

    def test_tool_result_json_not_corrupted_on_overflow(self):
        """Verify huge tool result gets an explicit truncation marker instead of sliced JSON."""
        conv = Conversation()
        huge_json = json.dumps({"vertices": [[float(i), float(i), 0.0] for i in range(2000)]})
        tc = ToolCall(call_id="call_mesh", tool_name="inspect_mesh", arguments={"name": "Cube"})

        conv.add_message(ChatMessage(role=Role.USER, content="Mesh analizi yap"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        conv.add_message(ChatMessage(role=Role.TOOL, content=huge_json, tool_call_id="call_mesh", name="inspect_mesh"))

        # Cap limit to 1000 characters (far less than huge_json's ~40,000 chars)
        ctx = ContextBuilder.build(conversation=conv, max_context_chars=1000)

        tool_msg = [m for m in ctx.messages if m.role == Role.TOOL][0]
        self.assertIn("[TRUNCATED:", tool_msg.content)
        self.assertIn("Full result omitted to prevent context overflow", tool_msg.content)
        # Verify it didn't leave half a bracket or broken syntax
        self.assertNotIn('[[0.0, 0.0, 0.0]', tool_msg.content)

    def test_tool_mapping_in_context_builder(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Test"))
        tools = [InspectSceneTool(), InspectObjectTool()]

        ctx = ContextBuilder.build(conversation=conv, tools=tools)
        self.assertEqual(len(ctx.tools), 2)
        tool_names = [t["function"]["name"] for t in ctx.tools]
        self.assertEqual(tool_names, ["inspect_scene", "inspect_object"])

    def test_deterministic_output_and_serialization(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Deterministic test"))
        tools = [InspectSceneTool()]

        ctx1 = ContextBuilder.build(conversation=conv, tools=tools)
        ctx2 = ContextBuilder.build(conversation=conv, tools=tools)

        d1 = ctx1.to_dict()
        d2 = ctx2.to_dict()
        self.assertEqual(d1, d2)

        json_str = json.dumps(d1, sort_keys=True)
        reconstructed = ProviderRequestContext.from_dict(json.loads(json_str))
        self.assertEqual(ctx1, reconstructed)


if __name__ == "__main__":
    unittest.main()
