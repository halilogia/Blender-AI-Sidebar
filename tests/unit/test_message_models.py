"""Unit tests for M2.2: Internal ChatMessage, Conversation, and Provider Stream Event models.

Zero Blender dependencies. Pure Python.
"""

from dataclasses import FrozenInstanceError
import json
import unittest

from core.types import ToolError
from agent.models import (
    AgentResult,
    ChatMessage,
    Conversation,
    ProviderCompleted,
    ProviderError,
    ProviderErrorType,
    ProviderResponse,
    ProviderStreamEvent,
    Role,
    TextDelta,
    ToolCall,
    ToolCallDelta,
)


class TestToolCall(unittest.TestCase):
    """Tests for ToolCall immutable dataclass."""

    def test_valid_tool_call(self):
        tc = ToolCall(call_id="call_abc123", tool_name="inspect_scene", arguments={"detail": "all"})
        self.assertEqual(tc.call_id, "call_abc123")
        self.assertEqual(tc.tool_name, "inspect_scene")
        self.assertEqual(tc.arguments, {"detail": "all"})

    def test_empty_call_id_rejected(self):
        with self.assertRaises(ValueError):
            ToolCall(call_id="", tool_name="inspect_scene", arguments={})
        with self.assertRaises(ValueError):
            ToolCall(call_id="   ", tool_name="inspect_scene", arguments={})

    def test_empty_tool_name_rejected(self):
        with self.assertRaises(ValueError):
            ToolCall(call_id="call_1", tool_name="", arguments={})
        with self.assertRaises(ValueError):
            ToolCall(call_id="call_1", tool_name="   ", arguments={})

    def test_arguments_not_dict_rejected(self):
        with self.assertRaises(TypeError):
            ToolCall(call_id="call_1", tool_name="inspect_scene", arguments="not_a_dict")
        with self.assertRaises(TypeError):
            ToolCall(call_id="call_1", tool_name="inspect_scene", arguments=[1, 2, 3])
        with self.assertRaises(TypeError):
            ToolCall(call_id="call_1", tool_name="inspect_scene", arguments=None)

    def test_tool_call_immutability(self):
        tc = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        with self.assertRaises(FrozenInstanceError):
            tc.call_id = "call_mutated"
        with self.assertRaises(FrozenInstanceError):
            tc.tool_name = "mutated_name"

    def test_tool_call_serialization_deterministic(self):
        tc = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={"z": 1, "a": 2, "m": 3})
        d = tc.to_dict()
        self.assertEqual(d["call_id"], "call_1")
        self.assertEqual(d["tool_name"], "inspect_scene")
        # Arguments keys should be deterministically sorted
        self.assertEqual(list(d["arguments"].keys()), ["a", "m", "z"])

        json_str = json.dumps(d)
        deserialized = ToolCall.from_dict(json.loads(json_str))
        self.assertEqual(tc, deserialized)


class TestChatMessage(unittest.TestCase):
    """Tests for ChatMessage role-based validation and serialization."""

    def test_system_message_valid(self):
        msg = ChatMessage(role=Role.SYSTEM, content="You are a Blender assistant.")
        self.assertEqual(msg.role, Role.SYSTEM)
        self.assertEqual(msg.content, "You are a Blender assistant.")
        self.assertIsNone(msg.tool_calls)
        self.assertIsNone(msg.tool_call_id)

    def test_system_message_rejects_tool_calls_or_id(self):
        tc = ToolCall(call_id="call_1", tool_name="tool_a", arguments={})
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.SYSTEM, content="sys", tool_calls=[tc])
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.SYSTEM, content="sys", tool_call_id="call_1")

    def test_user_message_valid(self):
        msg = ChatMessage(role=Role.USER, content="Cube'un meshini incele.")
        self.assertEqual(msg.role, Role.USER)
        self.assertEqual(msg.content, "Cube'un meshini incele.")

    def test_user_message_rejects_tool_calls_or_id(self):
        tc = ToolCall(call_id="call_1", tool_name="tool_a", arguments={})
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.USER, content="usr", tool_calls=[tc])
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.USER, content="usr", tool_call_id="call_1")

    def test_assistant_text_only(self):
        msg = ChatMessage(role=Role.ASSISTANT, content="İşte sahne analizi.")
        self.assertEqual(msg.role, Role.ASSISTANT)
        self.assertEqual(msg.content, "İşte sahne analizi.")
        self.assertIsNone(msg.tool_calls)

    def test_assistant_tool_calls_only(self):
        tc = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        msg = ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc])
        self.assertIsNone(msg.content)
        self.assertEqual(len(msg.tool_calls), 1)
        self.assertEqual(msg.tool_calls[0].tool_name, "inspect_scene")

    def test_assistant_both_text_and_tool_calls(self):
        tc = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        msg = ChatMessage(role=Role.ASSISTANT, content="Sahneyi inceliyorum...", tool_calls=[tc])
        self.assertEqual(msg.content, "Sahneyi inceliyorum...")
        self.assertEqual(len(msg.tool_calls), 1)

    def test_assistant_rejects_tool_call_id_or_empty_content_and_calls(self):
        tc = ToolCall(call_id="call_1", tool_name="tool_a", arguments={})
        # Cannot have tool_call_id
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.ASSISTANT, content="text", tool_call_id="call_1")
        # Cannot have both content=None and tool_calls=None
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=None)

    def test_tool_message_valid(self):
        msg = ChatMessage(
            role=Role.TOOL,
            content='{"objects": ["Cube", "Light"]}',
            tool_call_id="call_123",
            name="inspect_scene",
        )
        self.assertEqual(msg.role, Role.TOOL)
        self.assertEqual(msg.tool_call_id, "call_123")
        self.assertEqual(msg.name, "inspect_scene")
        self.assertIn("Cube", msg.content)

    def test_tool_message_validation_rules(self):
        # Missing content
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.TOOL, content=None, tool_call_id="call_1")
        # Missing tool_call_id
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.TOOL, content="{}", tool_call_id=None)
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.TOOL, content="{}", tool_call_id="")
        # Having tool_calls
        tc = ToolCall(call_id="call_1", tool_name="tool_a", arguments={})
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.TOOL, content="{}", tool_call_id="call_1", tool_calls=[tc])

    def test_role_coercion_and_invalid_role(self):
        # String coercion
        msg = ChatMessage(role="user", content="Hello")
        self.assertEqual(msg.role, Role.USER)

        with self.assertRaises(ValueError):
            ChatMessage(role="invalid_role", content="Hello")
        with self.assertRaises(TypeError):
            ChatMessage(role=12345, content="Hello")

    def test_chat_message_immutability(self):
        msg = ChatMessage(role=Role.USER, content="Original")
        with self.assertRaises(FrozenInstanceError):
            msg.content = "Mutated"

    def test_chat_message_serialization_round_trip(self):
        tc = ToolCall(call_id="call_1", tool_name="inspect_object", arguments={"name": "Cube"})
        msg = ChatMessage(
            role=Role.ASSISTANT,
            content="Checking object...",
            tool_calls=[tc],
        )
        d = msg.to_dict()
        self.assertEqual(d["role"], "assistant")
        self.assertEqual(d["content"], "Checking object...")
        self.assertEqual(len(d["tool_calls"]), 1)
        self.assertEqual(d["tool_calls"][0]["call_id"], "call_1")

        json_str = json.dumps(d)
        reconstructed = ChatMessage.from_dict(json.loads(json_str))
        self.assertEqual(reconstructed.role, Role.ASSISTANT)
        self.assertEqual(reconstructed.content, "Checking object...")
        self.assertEqual(len(reconstructed.tool_calls), 1)
        self.assertEqual(reconstructed.tool_calls[0].call_id, "call_1")


class TestConversation(unittest.TestCase):
    """Tests for Conversation container, order, and sequence validation."""

    def test_conversation_basic_operations(self):
        conv = Conversation()
        self.assertEqual(len(conv), 0)
        self.assertIsNone(conv.last())

        m1 = ChatMessage(role=Role.SYSTEM, content="System prompt")
        m2 = ChatMessage(role=Role.USER, content="User prompt")
        conv.add_message(m1)
        conv.add_message(m2)

        self.assertEqual(len(conv), 2)
        self.assertEqual(conv[0], m1)
        self.assertEqual(conv.last(), m2)

        # Messages list is a copy (encapsulation preserved)
        msgs = conv.messages
        msgs.clear()
        self.assertEqual(len(conv), 2)

        conv.clear()
        self.assertEqual(len(conv), 0)
        self.assertIsNone(conv.last())

    def test_find_tool_call(self):
        conv = Conversation()
        tc1 = ToolCall(call_id="call_scene", tool_name="inspect_scene", arguments={})
        tc2 = ToolCall(call_id="call_obj", tool_name="inspect_object", arguments={"name": "Cube"})

        conv.add_message(ChatMessage(role=Role.USER, content="Inspect scene and object"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1, tc2]))

        self.assertEqual(conv.find_tool_call("call_scene"), tc1)
        self.assertEqual(conv.find_tool_call("call_obj"), tc2)
        self.assertIsNone(conv.find_tool_call("call_non_existent"))

    def test_valid_multi_tool_sequence(self):
        """Test sequence: SYSTEM -> USER -> ASSISTANT(tool_calls) -> TOOL -> TOOL -> ASSISTANT(final)."""
        conv = Conversation()
        tc1 = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        tc2 = ToolCall(call_id="call_2", tool_name="inspect_selection", arguments={})

        conv.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
        conv.add_message(ChatMessage(role=Role.USER, content="Inspect scene and selection"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Inspecting...", tool_calls=[tc1, tc2]))
        conv.add_message(ChatMessage(role=Role.TOOL, content='{"scene": "Scene"}', tool_call_id="call_1", name="inspect_scene"))
        conv.add_message(ChatMessage(role=Role.TOOL, content='{"selection": ["Cube"]}', tool_call_id="call_2", name="inspect_selection"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="All tools executed successfully."))

        # Should validate without error
        conv.validate_sequence()

    def test_sequence_fails_on_unmatched_tool_call_id(self):
        conv = Conversation()
        tc = ToolCall(call_id="call_expected", tool_name="inspect_scene", arguments={})
        conv.add_message(ChatMessage(role=Role.USER, content="Test"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        conv.add_message(ChatMessage(role=Role.TOOL, content='{}', tool_call_id="call_wrong"))

        with self.assertRaises(ValueError) as ctx:
            conv.validate_sequence()
        self.assertIn("does not match any pending tool call", str(ctx.exception))

    def test_sequence_fails_when_tool_call_pending_on_assistant_turn(self):
        conv = Conversation()
        tc1 = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        tc2 = ToolCall(call_id="call_2", tool_name="inspect_selection", arguments={})
        conv.add_message(ChatMessage(role=Role.USER, content="Test"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1, tc2]))
        # Only 1 tool answered, second remains unresolved
        conv.add_message(ChatMessage(role=Role.TOOL, content='{}', tool_call_id="call_1"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Premature final answer."))

        with self.assertRaises(ValueError) as ctx:
            conv.validate_sequence()
        self.assertIn("still pending", str(ctx.exception))

    def test_sequence_fails_when_conversation_ends_with_pending_tools(self):
        conv = Conversation()
        tc = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        conv.add_message(ChatMessage(role=Role.USER, content="Test"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))

        with self.assertRaises(ValueError) as ctx:
            conv.validate_sequence()
        self.assertIn("unresolved pending tool calls", str(ctx.exception))

    def test_sequence_fails_on_orphan_tool_result(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Test"))
        conv.add_message(ChatMessage(role=Role.TOOL, content='{}', tool_call_id="call_orphan"))

        with self.assertRaises(ValueError) as ctx:
            conv.validate_sequence()
        self.assertIn("has no matching preceding ASSISTANT tool call", str(ctx.exception))

    def test_conversation_serialization_round_trip(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
        conv.add_message(ChatMessage(role=Role.USER, content="Hello"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, content="Hi"))

        conv_dict = conv.to_dict()
        json_str = json.dumps(conv_dict)
        reconstructed = Conversation.from_dict(json.loads(json_str))

        self.assertEqual(len(reconstructed), 3)
        self.assertEqual(reconstructed[0].content, "Sys")
        self.assertEqual(reconstructed[1].content, "Hello")
        self.assertEqual(reconstructed[2].content, "Hi")


class TestProviderStreamEvents(unittest.TestCase):
    """Tests for ProviderStreamEvent models (TextDelta, ToolCallDelta, ProviderCompleted, ProviderError)."""

    def test_text_delta(self):
        delta = TextDelta(turn_id="turn_1", text="Merhaba")
        self.assertIsInstance(delta, ProviderStreamEvent)
        self.assertEqual(delta.turn_id, "turn_1")
        self.assertEqual(delta.text, "Merhaba")

        d = delta.to_dict()
        self.assertEqual(d["event"], "text_delta")
        self.assertEqual(d["text"], "Merhaba")

        reconstructed = TextDelta.from_dict(d)
        self.assertEqual(delta, reconstructed)

    def test_text_delta_type_validation(self):
        with self.assertRaises(TypeError):
            TextDelta(turn_id="turn_1", text=12345)

    def test_tool_call_delta_partial_fragments(self):
        # 1. First fragment with index, call_id, and tool_name
        d1 = ToolCallDelta(turn_id="turn_1", index=0, call_id="call_abc", tool_name_delta="inspect_")
        self.assertEqual(d1.index, 0)
        self.assertEqual(d1.call_id, "call_abc")
        self.assertEqual(d1.tool_name_delta, "inspect_")
        self.assertIsNone(d1.arguments_delta)

        # 2. Second fragment with remaining tool_name
        d2 = ToolCallDelta(turn_id="turn_1", index=0, tool_name_delta="scene")
        self.assertIsNone(d2.call_id)
        self.assertEqual(d2.tool_name_delta, "scene")

        # 3. Third fragment with partial arguments
        d3 = ToolCallDelta(turn_id="turn_1", index=0, arguments_delta='{"name": "')
        self.assertEqual(d3.arguments_delta, '{"name": "')

        # 4. Negative index rejected
        with self.assertRaises(ValueError):
            ToolCallDelta(turn_id="turn_1", index=-1)

        # 5. Serialization
        dict_d1 = d1.to_dict()
        self.assertEqual(dict_d1["event"], "tool_call_delta")
        self.assertEqual(dict_d1["index"], 0)
        self.assertEqual(dict_d1["call_id"], "call_abc")

        reconstructed = ToolCallDelta.from_dict(dict_d1)
        self.assertEqual(d1, reconstructed)

    def test_provider_completed(self):
        comp = ProviderCompleted(
            turn_id="turn_1",
            finish_reason="stop",
            usage={"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
        )
        self.assertEqual(comp.finish_reason, "stop")
        self.assertEqual(comp.usage["total_tokens"], 40)

        d = comp.to_dict()
        self.assertEqual(d["event"], "provider_completed")
        self.assertEqual(d["finish_reason"], "stop")
        self.assertEqual(d["usage"]["prompt_tokens"], 15)

        reconstructed = ProviderCompleted.from_dict(d)
        self.assertEqual(comp, reconstructed)

    def test_provider_completed_without_usage(self):
        comp = ProviderCompleted(turn_id="turn_1", finish_reason="tool_calls", usage=None)
        self.assertIsNone(comp.usage)
        d = comp.to_dict()
        self.assertNotIn("usage", d)

    def test_provider_error(self):
        err = ProviderError(
            turn_id="turn_1",
            type=ProviderErrorType.AUTH_ERROR,
            message="Invalid API key provided.",
            details={"status_code": 401},
        )
        self.assertIsInstance(err, ProviderStreamEvent)
        self.assertNotIsInstance(err, ToolError)  # Distinct boundary check
        self.assertEqual(err.type, ProviderErrorType.AUTH_ERROR)
        self.assertEqual(err.message, "Invalid API key provided.")

        d = err.to_dict()
        self.assertEqual(d["event"], "provider_error")
        self.assertEqual(d["type"], "AUTH_ERROR")
        self.assertEqual(d["message"], "Invalid API key provided.")
        self.assertEqual(d["details"]["status_code"], 401)

        reconstructed = ProviderError.from_dict(d)
        self.assertEqual(reconstructed.type, "AUTH_ERROR")
        self.assertEqual(reconstructed.message, "Invalid API key provided.")


class TestEdgeCasesAndUnicode(unittest.TestCase):
    """Tests for edge cases, unicode, empty payloads, and boundary conditions."""

    def test_unicode_content_in_messages_and_tools(self):
        unicode_str = "Türkçe karakterler: çığıöşü / Emojiler: 🤖🎨✨ / 日本語: こんにちは"
        tc = ToolCall(call_id="call_ünicode_1", tool_name="inspect_scene", arguments={"açıklama": unicode_str})
        msg = ChatMessage(role=Role.USER, content=unicode_str)

        json_msg = json.dumps(msg.to_dict(), ensure_ascii=False)
        self.assertIn("çığıöşü", json_msg)
        self.assertIn("🤖🎨✨", json_msg)
        self.assertIn("こんにちは", json_msg)

        json_tc = json.dumps(tc.to_dict(), ensure_ascii=False)
        self.assertIn("call_ünicode_1", json_tc)
        self.assertIn("açıklama", json_tc)

    def test_empty_arguments_dict_in_tool_call(self):
        tc = ToolCall(call_id="call_1", tool_name="inspect_selection", arguments={})
        self.assertEqual(tc.arguments, {})
        d = tc.to_dict()
        self.assertEqual(d["arguments"], {})

    def test_multiple_sequential_tool_calls_in_assistant(self):
        tcs = [
            ToolCall(call_id=f"call_{i}", tool_name=f"tool_{i}", arguments={"idx": i})
            for i in range(5)
        ]
        msg = ChatMessage(role=Role.ASSISTANT, content="5 tools requested", tool_calls=tcs)
        self.assertEqual(len(msg.tool_calls), 5)
        d = msg.to_dict()
        self.assertEqual(len(d["tool_calls"]), 5)
        self.assertEqual(d["tool_calls"][4]["call_id"], "call_4")


if __name__ == "__main__":
    unittest.main()
