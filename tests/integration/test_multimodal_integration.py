"""Integration and acceptance suite for M7 Task 2: Multimodal Provider Integration in Blender 5.2.1.

Verifies:
1. Viewport screenshot capture via BlenderAdapter producing real PNG bytes.
2. Thread safety: get_viewport_screenshot raises ThreadSafetyViolationError on worker thread.
3. Clean in-memory pipeline: capture_viewport -> image_id -> PNG bytes -> ProviderRequestContext.
4. Multimodal payload assembly: OpenAIRequestMapper maps real PNG bytes into base64 data URI.
5. In-memory safety: ZERO temporary image files written to filesystem.
6. Unsupported multimodal provider deterministic rejection (MultimodalUnsupportedError / PROVIDER_UNSUPPORTED).
7. AgentRuntime.submit_prompt with image_id populating ProviderRequestContext on main thread.
"""

import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from agent.context_builder import ContextBuilder, ImageResolutionError, ProviderRequestContext
from agent.dispatcher import ToolDispatcher
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
from tools.read_only.capture_viewport import CaptureViewportTool
from tools.registry import ToolRegistry


class TestMultimodalIntegration(unittest.TestCase):
    """Headless integration tests for multimodal provider integration."""

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=False)
        self.registry = ToolRegistry()
        self.tool = CaptureViewportTool()
        self.registry.register(self.tool)
        self.adapter = BlenderAdapter()
        self.dispatcher = ToolDispatcher(self.registry, self.adapter)

    def test_01_real_viewport_capture_to_provider_request_chain(self):
        """End-to-end chain: capture viewport -> image_id -> PNG bytes -> OpenAI payload."""
        # 1. Capture real Blender 3D Viewport offscreen render
        res = self.adapter.capture_viewport(width=128, height=128)
        self.assertTrue(res.success, f"capture_viewport failed: {res.error}")
        image_id = res.data["image_id"]
        self.assertTrue(image_id.startswith("vp_"))

        # 2. Main thread retrieval of PNG bytes
        png_bytes = self.adapter.get_viewport_screenshot(image_id)
        self.assertIsNotNone(png_bytes)
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        # 3. Context assembly using image_resolver
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Explain scene", image_id=image_id))
        ctx = ContextBuilder.build(
            conversation=conv,
            tools=self.registry.list(),
            image_resolver=self.adapter.get_viewport_screenshot,
        )
        self.assertIn(image_id, ctx.images)
        self.assertEqual(ctx.images[image_id], png_bytes)

        # 4. Map to OpenAI payload
        payload = OpenAIRequestMapper.map_request(ctx, model="gpt-4o")
        messages = payload["messages"]
        # Index 0 is system, index 1 is user
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")

        user_content = messages[1]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual(user_content[0]["text"], "Explain scene")
        self.assertEqual(user_content[1]["type"], "image_url")

        data_url = user_content[1]["image_url"]["url"]
        self.assertTrue(data_url.startswith("data:image/png;base64,"))

        # Decode base64 and assert exact equality with rendered PNG bytes
        b64_str = data_url.split("data:image/png;base64,")[1]
        decoded = base64.b64decode(b64_str)
        self.assertEqual(decoded, png_bytes)

    def test_02_thread_safety_worker_cannot_call_get_viewport_screenshot(self):
        """Worker thread cannot invoke adapter.get_viewport_screenshot directly."""
        res = self.adapter.capture_viewport(width=128, height=128)
        self.assertTrue(res.success)
        image_id = res.data["image_id"]

        raised_error = []

        def worker_target():
            try:
                self.adapter.get_viewport_screenshot(image_id)
            except ThreadSafetyViolationError as exc:
                raised_error.append(exc)

        thread = threading.Thread(target=worker_target)
        thread.start()
        thread.join(timeout=1.0)

        self.assertEqual(len(raised_error), 1)
        self.assertIn("main thread", str(raised_error[0]).lower())

    def test_03_zero_filesystem_writes(self):
        """Verifies that no temporary PNG files or directories are created on disk."""
        temp_dir = tempfile.gettempdir()
        files_before = set(os.listdir(temp_dir))

        res = self.adapter.capture_viewport(width=128, height=128)
        self.assertTrue(res.success)
        image_id = res.data["image_id"]

        conv = Conversation([
            ChatMessage(role=Role.USER, content="Describe view", image_id=image_id)
        ])
        ctx = ContextBuilder.build(conv, image_resolver=self.adapter.get_viewport_screenshot)
        _ = OpenAIRequestMapper.map_request(ctx, model="gpt-4o")

        files_after = set(os.listdir(temp_dir))
        new_files = files_after - files_before
        # Ensure no png or image files were created in temp
        image_leaks = [f for f in new_files if f.endswith((".png", ".jpg", ".tmp"))]
        self.assertEqual(image_leaks, [])

    def test_04_unsupported_multimodal_provider_deterministic_rejection(self):
        """Provider with supports_multimodal=False deterministically yields PROVIDER_UNSUPPORTED."""
        res = self.adapter.capture_viewport(width=128, height=128)
        self.assertTrue(res.success)
        image_id = res.data["image_id"]

        conv = Conversation([
            ChatMessage(role=Role.USER, content="Describe", image_id=image_id)
        ])
        ctx = ContextBuilder.build(conv, image_resolver=self.adapter.get_viewport_screenshot)

        config = Config(base_url="http://localhost:11434/v1", model="text-only-model")
        provider = OpenAICompatibleProvider(config=config, supports_multimodal=False)

        events = list(provider.stream_chat(ctx, turn_id="turn_unsupported"))
        self.assertEqual(len(events), 1)
        err = events[0]
        self.assertIsInstance(err, ProviderError)
        self.assertEqual(err.type, ProviderErrorType.PROVIDER_UNSUPPORTED)

    def test_05_runtime_submit_prompt_with_image_id_main_thread(self):
        """AgentRuntime.submit_prompt with image_id resolves image on main thread."""
        res = self.adapter.capture_viewport(width=128, height=128)
        self.assertTrue(res.success)
        image_id = res.data["image_id"]

        # Mock provider that implements stream_chat
        mock_provider = MagicMock()
        mock_provider.stream_chat.return_value = iter([])

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
        )

        turn_id = runtime.submit_prompt(prompt="Analyze this capture", image_id=image_id)
        self.assertIsNotNone(turn_id)

        # Verify conversation history has image_id
        user_msg = runtime.conversation.messages[0]
        self.assertEqual(user_msg.role, Role.USER)
        self.assertEqual(user_msg.image_id, image_id)

    def test_06_capture_viewport_tool_result_to_next_provider_request_mapping(self):
        """Tool result from capture_viewport maps to tool string + accompanying user image message."""
        # 1. Dispatch capture_viewport tool
        tool_call = ToolCall(call_id="call_vp_real", tool_name="capture_viewport", arguments={"width": 128, "height": 128})
        tool_res = self.dispatcher.dispatch(tool_call)
        self.assertTrue(tool_res.success)
        image_id = tool_res.data["image_id"]

        # 2. Build conversation with tool execution turn
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Take a viewport screenshot"))
        conv.add_message(ChatMessage(role=Role.ASSISTANT, tool_calls=[tool_call]))
        conv.add_message(
            ChatMessage(
                role=Role.TOOL,
                content=json.dumps(tool_res.data),
                tool_call_id=tool_call.call_id,
                name="capture_viewport",
                image_id=image_id,
            )
        )

        # 3. Assemble context
        ctx = ContextBuilder.build(
            conversation=conv,
            tools=self.registry.list(),
            image_resolver=self.adapter.get_viewport_screenshot,
        )
        self.assertIn(image_id, ctx.images)
        png_bytes = ctx.images[image_id]

        # 4. Map to OpenAI payload
        payload = OpenAIRequestMapper.map_request(ctx, model="gpt-4o")
        msgs = payload["messages"]

        # Expected sequence:
        # [0] system
        # [1] user ("Take a viewport screenshot")
        # [2] assistant (tool_calls)
        # [3] tool (string content)
        # [4] user (accompanying image_url content-part)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[2]["role"], "assistant")

        tool_msg = msgs[3]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIsInstance(tool_msg["content"], str)
        self.assertEqual(tool_msg["tool_call_id"], "call_vp_real")

        user_img_msg = msgs[4]
        self.assertEqual(user_img_msg["role"], "user")
        self.assertIsInstance(user_img_msg["content"], list)
        self.assertEqual(user_img_msg["content"][0]["type"], "text")
        self.assertEqual(user_img_msg["content"][1]["type"], "image_url")

        # 5. Base64 integrity check
        data_url = user_img_msg["content"][1]["image_url"]["url"]
        b64_str = data_url.split("data:image/png;base64,")[1]
        self.assertEqual(base64.b64decode(b64_str), png_bytes)

        # 6. Hygiene check: zero raw binary bytes in serialized payload string
        payload_str = json.dumps(payload)
        self.assertNotIn(str(png_bytes), payload_str)

    def test_07_missing_image_cache_deterministic_failure(self):
        """Missing or expired image in cache produces deterministic ImageResolutionError and AgentState.ERROR."""
        conv = Conversation([
            ChatMessage(role=Role.USER, content="Explain this", image_id="vp_expired_or_invalid")
        ])

        # ContextBuilder raises ImageResolutionError
        with self.assertRaises(ImageResolutionError) as cm:
            ContextBuilder.build(conv, image_resolver=self.adapter.get_viewport_screenshot)
        self.assertEqual(cm.exception.image_id, "vp_expired_or_invalid")

        # Runtime transitions to ERROR and records in history
        mock_provider = MagicMock()
        runtime = AgentRuntime(provider=mock_provider, dispatcher=self.dispatcher)
        turn_id = runtime.submit_prompt("Look at scene", image_id="vp_expired_or_invalid")
        self.assertEqual(runtime.current_state, AgentState.ERROR)

        err_items = [h for h in runtime.history.items if h.kind == HistoryKind.ERROR]
        self.assertTrue(len(err_items) > 0)
        self.assertIn("IMAGE_NOT_FOUND", err_items[-1].title)


def run():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMultimodalIntegration)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
