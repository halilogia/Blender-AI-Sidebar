"""Unit tests for Multimodal Provider Integration (M7 Task 2).

Verifies:
1. ChatMessage and ProviderRequestContext multimodal data contracts.
2. OpenAIRequestMapper image payload mapping (Base64 data URI format).
3. Deterministic PROVIDER_UNSUPPORTED error for unsupported providers.
4. Clean end-to-end chain: capture_viewport -> image_id -> PNG bytes -> provider request.
5. In-memory safety: ZERO temporary image files written to disk.
6. Privacy & hygiene: NO raw image bytes or base64 dumps in history/logs.
"""

import base64
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from agent.context_builder import ContextBuilder, ImageResolutionError, ProviderRequestContext
from agent.history import HistoryKind
from agent.models import (
    ChatMessage,
    Conversation,
    ProviderError,
    ProviderErrorType,
    Role,
    ToolCall,
)
from agent.openai_provider import (
    MultimodalUnsupportedError,
    OpenAICompatibleProvider,
    OpenAIRequestMapper,
)
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from core.config import Config
from core.types import ToolResult


def make_dummy_png() -> bytes:
    """Return minimal valid PNG byte sequence (1x1 RGBA)."""
    # 1x1 PNG header + IHDR + IDAT + IEND
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class TestChatMessageMultimodal(unittest.TestCase):
    """Test ChatMessage image_id field validation and serialization."""

    def test_user_message_with_image_id(self):
        msg = ChatMessage(role=Role.USER, content="Analyze viewport", image_id="vp_test123")
        self.assertEqual(msg.role, Role.USER)
        self.assertEqual(msg.content, "Analyze viewport")
        self.assertEqual(msg.image_id, "vp_test123")

        d = msg.to_dict()
        self.assertEqual(d["image_id"], "vp_test123")

        restored = ChatMessage.from_dict(d)
        self.assertEqual(restored.image_id, "vp_test123")

    def test_tool_message_with_image_id(self):
        msg = ChatMessage(
            role=Role.TOOL,
            content='{"image_id": "vp_test123", "width": 512}',
            tool_call_id="call_vp_1",
            name="capture_viewport",
            image_id="vp_test123",
        )
        self.assertEqual(msg.image_id, "vp_test123")
        self.assertEqual(msg.to_dict()["image_id"], "vp_test123")

    def test_system_message_rejects_image_id(self):
        with self.assertRaises(ValueError):
            ChatMessage(role=Role.SYSTEM, content="System prompt", image_id="vp_forbidden")

    def test_invalid_image_id_type_raises(self):
        with self.assertRaises(TypeError):
            ChatMessage(role=Role.USER, content="Hello", image_id=12345)


class TestProviderRequestContextMultimodal(unittest.TestCase):
    """Test ProviderRequestContext handling of in-memory image attachments."""

    def test_request_context_images_and_serialization_safety(self):
        png_data = make_dummy_png()
        msg = ChatMessage(role=Role.USER, content="Look at this", image_id="vp_1")
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="sys",
            images={"vp_1": png_data},
        )
        self.assertEqual(ctx.images["vp_1"], png_data)

        # Ensure to_dict() NEVER serializes raw binary bytes into dictionary
        d = ctx.to_dict()
        self.assertIn("image_ids", d)
        self.assertEqual(d["image_ids"], ["vp_1"])
        self.assertNotIn("images", d)
        # Ensure json.dumps on to_dict() succeeds without binary encoding issues
        json_str = json.dumps(d)
        self.assertNotIn(str(png_data), json_str)


class TestContextBuilderMultimodal(unittest.TestCase):
    """Test ContextBuilder image resolution and in-memory population."""

    def test_resolve_images_via_image_resolver(self):
        png_data = make_dummy_png()
        image_store = {"vp_001": png_data}

        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Describe render", image_id="vp_001"))

        def mock_resolver(img_id: str):
            return image_store.get(img_id)

        ctx = ContextBuilder.build(
            conversation=conv,
            image_resolver=mock_resolver,
        )
        self.assertIn("vp_001", ctx.images)
        self.assertEqual(ctx.images["vp_001"], png_data)

    def test_resolve_images_from_capture_viewport_tool_result_content(self):
        png_data = make_dummy_png()
        image_store = {"vp_captured": png_data}

        conv = Conversation()
        conv.add_message(
            ChatMessage(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(call_id="call_c1", tool_name="capture_viewport", arguments={})],
            )
        )
        conv.add_message(
            ChatMessage(
                role=Role.TOOL,
                content=json.dumps({"image_id": "vp_captured", "width": 512, "height": 512}),
                tool_call_id="call_c1",
                name="capture_viewport",
            )
        )

        def mock_resolver(img_id: str):
            return image_store.get(img_id)

        ctx = ContextBuilder.build(
            conversation=conv,
            image_resolver=mock_resolver,
        )
        self.assertIn("vp_captured", ctx.images)
        self.assertEqual(ctx.images["vp_captured"], png_data)

    def test_unresolvable_image_id_raises_deterministic_error(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Describe", image_id="vp_missing"))

        def empty_resolver(img_id: str):
            return None

        with self.assertRaises(ImageResolutionError) as ctx_err:
            ContextBuilder.build(
                conversation=conv,
                image_resolver=empty_resolver,
            )
        self.assertEqual(ctx_err.exception.image_id, "vp_missing")

    def test_missing_image_resolver_with_image_id_raises_error(self):
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Describe", image_id="vp_missing"))

        with self.assertRaises(ImageResolutionError) as ctx_err:
            ContextBuilder.build(
                conversation=conv,
                image_resolver=None,
            )
        self.assertEqual(ctx_err.exception.image_id, "vp_missing")


class TestOpenAIRequestMapperMultimodal(unittest.TestCase):
    """Test OpenAIRequestMapper payload formatting for multimodal messages."""

    def test_text_only_regression(self):
        msg = ChatMessage(role=Role.USER, content="Hello text only")
        ctx = ProviderRequestContext(messages=[msg], tools=[], system_prompt="System")
        body = OpenAIRequestMapper.map_request(ctx, model="gpt-4o")

        # Must retain classic string content format
        user_msg = body["messages"][0]
        self.assertEqual(user_msg["role"], "user")
        self.assertEqual(user_msg["content"], "Hello text only")
        self.assertIsInstance(user_msg["content"], str)

    def test_user_multimodal_mapping_to_openai_format(self):
        png_data = make_dummy_png()
        msg = ChatMessage(role=Role.USER, content="What is in the viewport?", image_id="vp_test")
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="System",
            images={"vp_test": png_data},
        )
        body = OpenAIRequestMapper.map_request(ctx, model="gpt-4o")

        user_msg = body["messages"][0]
        self.assertEqual(user_msg["role"], "user")
        self.assertIsInstance(user_msg["content"], list)
        self.assertEqual(len(user_msg["content"]), 2)

        text_part = user_msg["content"][0]
        self.assertEqual(text_part["type"], "text")
        self.assertEqual(text_part["text"], "What is in the viewport?")

        image_part = user_msg["content"][1]
        self.assertEqual(image_part["type"], "image_url")
        self.assertIn("image_url", image_part)
        self.assertIn("url", image_part["image_url"])

        url = image_part["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))

        # Verify decoded base64 exactly matches original in-memory PNG bytes
        b64_str = url.split("data:image/png;base64,")[1]
        decoded = base64.b64decode(b64_str)
        self.assertEqual(decoded, png_data)

    def test_tool_multimodal_mapping_to_openai_format(self):
        png_data = make_dummy_png()
        tool_content_str = json.dumps({"image_id": "vp_img", "status": "OK"})
        msg = ChatMessage(
            role=Role.TOOL,
            content=tool_content_str,
            tool_call_id="call_vp",
            name="capture_viewport",
            image_id="vp_img",
        )
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="System",
            images={"vp_img": png_data},
        )
        body = OpenAIRequestMapper.map_request(ctx, model="gpt-4o")

        # OpenAI Chat Completions API spec:
        # role="tool" content MUST be string
        tool_msg = body["messages"][0]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(tool_msg["tool_call_id"], "call_vp")
        self.assertIsInstance(tool_msg["content"], str)
        self.assertEqual(tool_msg["content"], tool_content_str)

        # Accompanying user message carries the image_url content-part
        user_img_msg = body["messages"][1]
        self.assertEqual(user_img_msg["role"], "user")
        self.assertIsInstance(user_img_msg["content"], list)
        self.assertEqual(user_img_msg["content"][0]["type"], "text")
        self.assertIn("vp_img", user_img_msg["content"][0]["text"])

        image_part = user_img_msg["content"][1]
        self.assertEqual(image_part["type"], "image_url")
        b64_str = image_part["image_url"]["url"].split("data:image/png;base64,")[1]
        self.assertEqual(base64.b64decode(b64_str), png_data)

    def test_tool_with_image_id_missing_from_images_raises_image_resolution_error(self):
        msg = ChatMessage(
            role=Role.TOOL,
            content=json.dumps({"image_id": "vp_lost"}),
            tool_call_id="call_1",
            image_id="vp_lost",
        )
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="System",
            images={},  # missing
        )
        with self.assertRaises(ImageResolutionError) as cm:
            OpenAIRequestMapper.map_request(ctx, model="gpt-4o")
        self.assertEqual(cm.exception.image_id, "vp_lost")

    def test_unsupported_multimodal_raises_deterministic_error(self):
        png_data = make_dummy_png()
        msg = ChatMessage(role=Role.USER, content="Look", image_id="vp_1")
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="System",
            images={"vp_1": png_data},
        )
        with self.assertRaises(MultimodalUnsupportedError):
            OpenAIRequestMapper.map_request(
                ctx,
                model="deepseek-coder:base",
                supports_multimodal=False,
            )


class TestOpenAICompatibleProviderMultimodal(unittest.TestCase):
    """Test OpenAICompatibleProvider execution with multimodal requests."""

    def test_unsupported_multimodal_yields_provider_error(self):
        config = Config(
            base_url="http://localhost:11434/v1",
            model="text-only-model",
        )
        provider = OpenAICompatibleProvider(config=config, supports_multimodal=False)

        png_data = make_dummy_png()
        msg = ChatMessage(role=Role.USER, content="Look", image_id="vp_1")
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="System",
            images={"vp_1": png_data},
        )

        events = list(provider.stream_chat(ctx, turn_id="turn_test"))
        self.assertEqual(len(events), 1)
        err = events[0]
        self.assertIsInstance(err, ProviderError)
        self.assertEqual(err.type, ProviderErrorType.PROVIDER_UNSUPPORTED)
        self.assertIn("does not support multimodal", err.message)

    def test_missing_image_yields_image_not_found_provider_error(self):
        config = Config(
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        provider = OpenAICompatibleProvider(config=config, supports_multimodal=True)
        msg = ChatMessage(role=Role.USER, content="Look", image_id="vp_not_in_dict")
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="System",
            images={},  # Missing
        )

        events = list(provider.stream_chat(ctx, turn_id="turn_err"))
        self.assertEqual(len(events), 1)
        err = events[0]
        self.assertIsInstance(err, ProviderError)
        self.assertEqual(err.type, ProviderErrorType.IMAGE_NOT_FOUND)
        self.assertEqual(err.details.get("image_id"), "vp_not_in_dict")

    def test_multimodal_dispatches_proper_payload_without_disk_writes(self):
        config = Config(
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        mock_http = MagicMock()
        mock_response = MagicMock()
        mock_response.__enter__.return_value = [
            b'data: {"choices": [{"delta": {"content": "I see the viewport"}}] }\n\n',
            b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}] }\n\n',
            b'data: [DONE]\n\n',
        ]
        mock_http.post.return_value = mock_response

        provider = OpenAICompatibleProvider(config=config, http_client=mock_http, supports_multimodal=True)

        png_data = make_dummy_png()
        msg = ChatMessage(role=Role.USER, content="Analyze viewport", image_id="vp_screen")
        ctx = ProviderRequestContext(
            messages=[msg],
            tools=[],
            system_prompt="System",
            images={"vp_screen": png_data},
        )

        # Track directory file count to prove ZERO disk files created
        temp_dir = tempfile.gettempdir()
        initial_file_count = len(os.listdir(temp_dir))

        events = list(provider.stream_chat(ctx, turn_id="turn_v1"))

        # Verify no temporary files leaked to disk
        final_file_count = len(os.listdir(temp_dir))
        self.assertEqual(initial_file_count, final_file_count)

        # Verify HTTP POST payload received correct base64 data URI
        self.assertTrue(mock_http.post.called)
        call_kwargs = mock_http.post.call_args[1]
        payload = json.loads(call_kwargs["payload"].decode("utf-8"))

        user_content = payload["messages"][0]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual(user_content[0]["text"], "Analyze viewport")
        self.assertTrue(user_content[1]["image_url"]["url"].startswith("data:image/png;base64,"))


class TestEndToEndMultimodalChain(unittest.TestCase):
    """Test complete capture_viewport -> image_id -> PNG bytes -> provider request chain."""

    def test_full_chain_isolation_and_hygiene(self):
        # 1. Mock adapter simulating capture_viewport tool result and cached screenshot
        png_data = make_dummy_png()
        image_id = "vp_deterministic_123"

        class MockAdapter:
            def capture_viewport(self, width: int = 512, height: int = 512) -> ToolResult:
                return ToolResult.ok(
                    "capture_viewport",
                    {
                        "image_id": image_id,
                        "width": width,
                        "height": height,
                        "format": "PNG",
                        "mime_type": "image/png",
                        "byte_size": len(png_data),
                    },
                )

            def get_viewport_screenshot(self, query_id: str):
                if query_id == image_id:
                    return png_data
                return None

        adapter = MockAdapter()

        # 2. Tool invocation produces ToolResult
        tool_res = adapter.capture_viewport()
        self.assertTrue(tool_res.success)
        retrieved_id = tool_res.data["image_id"]
        self.assertEqual(retrieved_id, image_id)

        # 3. ChatMessage preserves image_id metadata without raw byte dumps
        tool_msg = ChatMessage(
            role=Role.TOOL,
            content=json.dumps(tool_res.data),
            tool_call_id="call_vp_01",
            name="capture_viewport",
            image_id=retrieved_id,
        )
        msg_dict = tool_msg.to_dict()
        self.assertNotIn("base64", json.dumps(msg_dict))

        # 4. ContextBuilder resolves PNG bytes using adapter.get_viewport_screenshot
        conv = Conversation([tool_msg])
        ctx = ContextBuilder.build(
            conversation=conv,
            image_resolver=adapter.get_viewport_screenshot,
        )
        self.assertIn(image_id, ctx.images)
        self.assertEqual(ctx.images[image_id], png_data)

        # 5. OpenAIRequestMapper builds valid multimodal payload
        payload = OpenAIRequestMapper.map_request(ctx, model="gpt-4o")
        self.assertEqual(payload["messages"][0]["role"], "system")
        
        # Tool message has string JSON content
        mapped_tool_msg = payload["messages"][1]
        self.assertEqual(mapped_tool_msg["role"], "tool")
        self.assertIsInstance(mapped_tool_msg["content"], str)
        self.assertEqual(mapped_tool_msg["tool_call_id"], "call_vp_01")

        # Accompanying user message has image_url content part
        mapped_user_msg = payload["messages"][2]
        self.assertEqual(mapped_user_msg["role"], "user")
        self.assertIsInstance(mapped_user_msg["content"], list)
        image_part = mapped_user_msg["content"][1]
        self.assertEqual(image_part["type"], "image_url")

        # 6. Verify Base64 integrity
        encoded_url = image_part["image_url"]["url"]
        b64_str = encoded_url.split("data:image/png;base64,")[1]
        self.assertEqual(base64.b64decode(b64_str), png_data)

        # 7. Verify no raw binary bytes in serialized payload string
        payload_str = json.dumps(payload)
        self.assertNotIn(str(png_data), payload_str)


class TestRuntimeMultimodalErrorHandling(unittest.TestCase):
    """Test AgentRuntime behavior when image resolution fails deterministically."""

    def test_runtime_submit_prompt_missing_image_transitions_to_error(self):
        mock_provider = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_viewport_screenshot.return_value = None
        mock_dispatcher = MagicMock()
        mock_dispatcher.adapter = mock_adapter
        mock_dispatcher.registry.list.return_value = []
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        conv = runtime.conversation
        conv.add_message(ChatMessage(role=Role.USER, content="Look", image_id="vp_gone"))

        # Submit prompt when image cannot be resolved from cache
        turn_id = runtime.submit_prompt("Next prompt")
        self.assertEqual(runtime.current_state, AgentState.ERROR)

        # Verify history recorded the deterministic error
        err_items = [h for h in runtime.history.items if h.kind == HistoryKind.ERROR]
        self.assertTrue(len(err_items) > 0)
        self.assertIn("IMAGE_NOT_FOUND", err_items[-1].title)


if __name__ == "__main__":
    unittest.main()
