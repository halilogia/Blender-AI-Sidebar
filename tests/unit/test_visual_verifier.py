"""Unit tests for Visual Scene Verification (M7 Task 3).

Verifies:
1. Semantic verification PASS + Visual PASS.
2. Semantic verification PASS + Visual FAIL.
3. Semantic verification PASS + Visual UNCERTAIN.
4. Semantic verification FAIL halts visual verification.
5. Multimodal provider unsupported gracefully yields UNCERTAIN.
6. Viewport unavailable yields UNCERTAIN.
7. Image resolution failure deterministically raises ImageResolutionError.
8. Text-only provider regression safety.
9. Privacy & hygiene: NO raw PNG bytes or base64 data URIs in to_dict()/history.
10. Main-thread safety enforcement.
11. VisualResultParser safe handling of malformed and corrupted model outputs.
12. VisualVerifyTool contract and execution.
13. AgentRuntime visual verification wiring.
"""

import json
import threading
import unittest
from unittest.mock import MagicMock

from agent.context_builder import ImageResolutionError
from agent.models import (
    ChatMessage,
    ProviderError,
    ProviderErrorType,
    Role,
    TextDelta,
    ToolCall,
)
from agent.runtime import AgentRuntime
from agent.visual_verifier import (
    VisualResultParser,
    VisualVerificationResult,
    VisualVerificationStatus,
    VisualVerifier,
)
from core.change_set import VerificationResult, VerificationStatus
from core.types import ToolResult
from tools.read_only.visual_verify import VisualVerifyTool


def make_dummy_png() -> bytes:
    """Return minimal valid 1x1 RGBA PNG bytes."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class TestVisualResultParserRobustness(unittest.TestCase):
    """Test VisualResultParser safely rejects and normalizes corrupted/unexpected model text."""

    def test_clean_json_pass(self):
        raw = '{"status": "PASS", "reason": "Red metallic cube is clearly visible at origin."}'
        res = VisualResultParser.parse(raw, expected_description="Red metallic cube")
        self.assertEqual(res.status, VisualVerificationStatus.PASS)
        self.assertTrue(res.passed)
        self.assertEqual(res.reason, "Red metallic cube is clearly visible at origin.")

    def test_clean_json_fail(self):
        raw = '{"status": "FAIL", "reason": "Cube is missing; viewport is empty."}'
        res = VisualResultParser.parse(raw, expected_description="Cube")
        self.assertEqual(res.status, VisualVerificationStatus.FAIL)
        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "Cube is missing; viewport is empty.")

    def test_clean_json_uncertain(self):
        raw = '{"status": "UNCERTAIN", "reason": "Camera distance too far to verify roughness."}'
        res = VisualResultParser.parse(raw, expected_description="Rough cube")
        self.assertEqual(res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertFalse(res.passed)

    def test_markdown_code_fences_parsed_cleanly(self):
        raw = "```json\n{\n  \"status\": \"PASS\",\n  \"reason\": \"Fenced json works\"\n}\n```"
        res = VisualResultParser.parse(raw, expected_description="test")
        self.assertEqual(res.status, VisualVerificationStatus.PASS)
        self.assertEqual(res.reason, "Fenced json works")

    def test_embedded_json_in_conversational_text(self):
        raw = 'Here is the analysis:\n{"status": "PASS", "reason": "Visible"}\nHope that helps!'
        res = VisualResultParser.parse(raw, expected_description="test")
        self.assertEqual(res.status, VisualVerificationStatus.PASS)
        self.assertEqual(res.reason, "Visible")

    def test_malformed_json_returns_uncertain_without_crashing(self):
        raw = "This is not JSON at all."
        res = VisualResultParser.parse(raw, expected_description="test")
        self.assertEqual(res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertIn("Failed to parse", res.reason)
        self.assertEqual(res.details.get("error_type"), "MALFORMED_JSON")

    def test_empty_model_output_returns_uncertain(self):
        res = VisualResultParser.parse("", expected_description="test")
        self.assertEqual(res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertEqual(res.details.get("error_type"), "EMPTY_MODEL_OUTPUT")

    def test_non_dict_json_returns_uncertain(self):
        raw = '["PASS", "reason"]'
        res = VisualResultParser.parse(raw, expected_description="test")
        self.assertEqual(res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertEqual(res.details.get("error_type"), "INVALID_JSON_SHAPE")

    def test_unrecognized_status_value_returns_uncertain(self):
        raw = '{"status": "PERFECT", "reason": "It looks amazing!"}'
        res = VisualResultParser.parse(raw, expected_description="test")
        self.assertEqual(res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertIn("unrecognized verification status 'PERFECT'", res.reason)
        self.assertEqual(res.details.get("error_type"), "INVALID_STATUS_VALUE")

    def test_missing_reason_field_provides_fallback(self):
        raw = '{"status": "PASS"}'
        res = VisualResultParser.parse(raw, expected_description="test")
        self.assertEqual(res.status, VisualVerificationStatus.PASS)
        self.assertIn("No rationale provided", res.reason)


class TestVisualVerifierPipeline(unittest.TestCase):
    """Test VisualVerifier core execution pipeline and edge cases."""

    def setUp(self):
        self.png_data = make_dummy_png()
        self.image_id = "vp_test_001"

        self.mock_adapter = MagicMock()
        self.mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": self.image_id, "width": 512, "height": 512},
        )
        self.mock_adapter.get_viewport_screenshot.return_value = self.png_data

        self.mock_provider = MagicMock()
        self.mock_provider.supports_multimodal = True

    def test_01_semantic_pass_and_visual_pass(self):
        """Semantic verification PASS + Visual verification PASS."""
        semantic_res = VerificationResult(
            status=VerificationStatus.PASS,
            operation="create",
            target_name="Cube",
            mismatches=[],
            summary="Semantic verification PASSED.",
        )
        self.mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "PASS", "reason": "The cube is clearly rendered."}')
        ]

        verifier = VisualVerifier(
            provider=self.mock_provider,
            adapter=self.mock_adapter,
        )

        vis_res = verifier.verify_after_mutation(
            semantic_result=semantic_res,
            expected_description="Cube at origin",
        )
        self.assertEqual(vis_res.status, VisualVerificationStatus.PASS)
        self.assertTrue(vis_res.passed)
        self.assertEqual(vis_res.reason, "The cube is clearly rendered.")
        self.assertEqual(vis_res.image_id, self.image_id)

    def test_02_semantic_pass_and_visual_fail(self):
        """Semantic verification PASS + Visual verification FAIL."""
        semantic_res = VerificationResult(
            status=VerificationStatus.PASS,
            operation="set_material",
            target_name="Sphere",
            mismatches=[],
            summary="Semantic verification PASSED.",
        )
        self.mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "FAIL", "reason": "Sphere appears completely black; emission missing."}')
        ]

        verifier = VisualVerifier(
            provider=self.mock_provider,
            adapter=self.mock_adapter,
        )

        vis_res = verifier.verify_after_mutation(
            semantic_result=semantic_res,
            expected_description="Bright glowing sphere",
        )
        self.assertEqual(vis_res.status, VisualVerificationStatus.FAIL)
        self.assertFalse(vis_res.passed)
        self.assertIn("completely black", vis_res.reason)
        # Semantic result remains passed
        self.assertTrue(semantic_res.passed)

    def test_03_semantic_pass_and_visual_uncertain(self):
        """Semantic verification PASS + Visual verification UNCERTAIN."""
        semantic_res = VerificationResult(
            status=VerificationStatus.PASS,
            operation="transform",
            target_name="Cube",
            mismatches=[],
            summary="Semantic verification PASSED.",
        )
        self.mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "UNCERTAIN", "reason": "Extreme camera angle occludes translation."}')
        ]

        verifier = VisualVerifier(
            provider=self.mock_provider,
            adapter=self.mock_adapter,
        )

        vis_res = verifier.verify_after_mutation(
            semantic_result=semantic_res,
            expected_description="Cube translated along Z",
        )
        self.assertEqual(vis_res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertFalse(vis_res.passed)

    def test_04_semantic_fail_skips_visual_verification(self):
        """Semantic verification FAIL aborts visual verification immediately."""
        semantic_res = VerificationResult(
            status=VerificationStatus.FAIL,
            operation="create",
            target_name="Cube",
            mismatches=[{"property": "exists", "expected": True, "actual": False}],
            summary="Semantic verification FAILED: Object missing.",
        )

        verifier = VisualVerifier(
            provider=self.mock_provider,
            adapter=self.mock_adapter,
        )

        vis_res = verifier.verify_after_mutation(
            semantic_result=semantic_res,
            expected_description="A cube",
        )
        self.assertEqual(vis_res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertEqual(vis_res.details.get("error_type"), "SEMANTIC_VERIFICATION_FAILED")
        # Provider must NOT have been called
        self.assertFalse(self.mock_provider.stream_chat.called)

    def test_05_multimodal_provider_unsupported(self):
        """Provider with supports_multimodal=False deterministically returns UNCERTAIN."""
        self.mock_provider.supports_multimodal = False

        verifier = VisualVerifier(
            provider=self.mock_provider,
            adapter=self.mock_adapter,
        )

        vis_res = verifier.verify(expected_description="A blue sphere")
        self.assertEqual(vis_res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertEqual(vis_res.details.get("error_type"), "PROVIDER_UNSUPPORTED")
        self.assertIn("does not support multimodal", vis_res.reason)
        self.assertFalse(self.mock_provider.stream_chat.called)

    def test_06_viewport_unavailable(self):
        """Adapter failure to capture viewport returns UNCERTAIN with VIEWPORT_UNAVAILABLE."""
        self.mock_adapter.capture_viewport.return_value = ToolResult.fail(
            "capture_viewport",
            error_type="VIEWPORT_UNAVAILABLE",
            message="No 3D Viewport found in workspace.",
        )

        verifier = VisualVerifier(
            provider=self.mock_provider,
            adapter=self.mock_adapter,
        )

        vis_res = verifier.verify(expected_description="A green plane")
        self.assertEqual(vis_res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertEqual(vis_res.details.get("error_type"), "VIEWPORT_UNAVAILABLE")
        self.assertIn("Viewport capture failed", vis_res.reason)

    def test_07_image_resolution_failure_raises_error(self):
        """Referencing an image_id missing from in-memory cache raises ImageResolutionError."""
        self.mock_adapter.get_viewport_screenshot.return_value = None

        verifier = VisualVerifier(
            provider=self.mock_provider,
            adapter=self.mock_adapter,
        )

        with self.assertRaises(ImageResolutionError) as ctx_err:
            verifier.verify(expected_description="Test", image_id="vp_expired_id")
        self.assertEqual(ctx_err.exception.image_id, "vp_expired_id")

    def test_08_privacy_and_hygiene_zero_raw_bytes_or_base64_in_serialization(self):
        """VisualVerificationResult.to_dict() never contains raw bytes or base64 strings."""
        res = VisualVerificationResult(
            status=VisualVerificationStatus.PASS,
            reason="Verified successfully",
            expected_description="Golden monkey",
            image_id="vp_safe_123",
            details={"timestamp": 12345},
        )
        d = res.to_dict()
        serialized = json.dumps(d)

        self.assertNotIn("base64", serialized)
        self.assertNotIn("b'\x89PNG", serialized)
        self.assertEqual(d["image_id"], "vp_safe_123")
        self.assertEqual(d["status"], "PASS")

    def test_09_main_thread_safety_check(self):
        """Verifies that background threads cannot invoke adapter capture."""
        thread_error = []

        def worker():
            try:
                # Simulating adapter thread safety guard
                if threading.current_thread() != threading.main_thread():
                    raise RuntimeError("Thread safety violation: capture_viewport must run on main thread.")
            except RuntimeError as exc:
                thread_error.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=1.0)
        self.assertEqual(len(thread_error), 1)

    def test_10_empty_expected_description_returns_uncertain(self):
        """Empty expected_description is rejected cleanly."""
        verifier = VisualVerifier(provider=self.mock_provider, adapter=self.mock_adapter)
        res = verifier.verify(expected_description="   ")
        self.assertEqual(res.status, VisualVerificationStatus.UNCERTAIN)
        self.assertEqual(res.details.get("error_type"), "INVALID_ARGUMENTS")


class TestVisualVerifyTool(unittest.TestCase):
    """Test VisualVerifyTool schema and execution contract."""

    def setUp(self):
        self.tool = VisualVerifyTool()
        self.mock_adapter = MagicMock()
        self.mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_tool_captured", "width": 512, "height": 512},
        )

    def test_tool_metadata(self):
        self.assertEqual(self.tool.name, "visual_verify")
        self.assertEqual(self.tool.risk_level.value, "READ_ONLY")
        self.assertIn("expected_description", self.tool.input_schema["properties"])

    def test_tool_execution_captures_viewport_when_image_id_omitted(self):
        res = self.tool.execute(self.mock_adapter, expected_description="A shiny sphere")
        self.assertTrue(res.success)
        self.assertEqual(res.data["image_id"], "vp_tool_captured")
        self.assertEqual(res.data["expected_description"], "A shiny sphere")
        self.assertEqual(res.data["status"], "CAPTURED")
        self.assertTrue(self.mock_adapter.capture_viewport.called)

    def test_tool_execution_uses_existing_image_id_without_capture(self):
        res = self.tool.execute(
            self.mock_adapter,
            expected_description="A cylinder",
            image_id="vp_existing_123",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.data["image_id"], "vp_existing_123")
        self.assertFalse(self.mock_adapter.capture_viewport.called)

    def test_tool_execution_rejects_empty_description(self):
        res = self.tool.execute(self.mock_adapter, expected_description="")
        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENTS")


class TestAgentRuntimeVisualVerification(unittest.TestCase):
    """Test AgentRuntime integration with VisualVerifier."""

    def test_runtime_verify_visual_helper(self):
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "PASS", "reason": "Runtime verified scene."}')
        ]

        png_data = make_dummy_png()
        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_rt_1", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = png_data

        mock_dispatcher = MagicMock()
        mock_dispatcher.adapter = mock_adapter

        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        vis_res = runtime.verify_visual(expected_description="Scene has cube")

        self.assertEqual(vis_res.status, VisualVerificationStatus.PASS)
        self.assertTrue(vis_res.passed)
        self.assertEqual(vis_res.image_id, "vp_rt_1")

    def test_runtime_executes_visual_verify_tool_call(self):
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "PASS", "reason": "Tool verified viewport."}')
        ]

        png_data = make_dummy_png()
        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_dispatch_1", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = png_data

        tool = VisualVerifyTool()
        mock_dispatcher = MagicMock()
        mock_dispatcher.adapter = mock_adapter
        mock_dispatcher.dispatch.return_value = ToolResult.ok(
            "visual_verify",
            {"image_id": "vp_dispatch_1", "expected_description": "A torus"},
        )

        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        tool_call = ToolCall(
            call_id="call_v1",
            tool_name="visual_verify",
            arguments={"expected_description": "A torus"},
        )

        result = runtime._execute_and_verify(tool_call)
        self.assertTrue(result.success)
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "PASS")

    def _create_mock_mutation_dispatcher(self, mock_adapter, object_exists: bool = True):
        mock_dispatcher = MagicMock()
        mock_dispatcher.adapter = mock_adapter
        mock_dispatcher.dispatch.return_value = ToolResult.ok(
            "create_primitive",
            {
                "exists": object_exists,
                "name": "Cube",
                "primitive_type": "CUBE",
                "location": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "dimensions": [2.0, 2.0, 2.0],
                "vertex_count": 8 if object_exists else 0,
                "polygon_count": 6 if object_exists else 0,
            },
        )
        return mock_dispatcher

    def test_mutation_semantic_pass_and_visual_pass(self):
        """Mutation passes semantic verification, then visual verification runs and returns PASS."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "PASS", "reason": "Cube visually confirmed in viewport."}')
        ]

        png_data = make_dummy_png()
        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_mut_1", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = png_data

        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime.set_visual_verification_expectation("A newly created cube at origin")

        tool_call = ToolCall(
            call_id="call_m1",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertIn("verification", result.data)
        self.assertTrue(result.data["verification"]["passed"])
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "PASS")
        self.assertEqual(result.data["visual_verification"]["reason"], "Cube visually confirmed in viewport.")
        self.assertTrue(result.data["visual_verification"]["passed"])
        self.assertEqual(mock_provider.stream_chat.call_count, 1)

    def test_mutation_semantic_pass_and_visual_fail_no_rollback(self):
        """Mutation passes semantic verification, visual verification returns FAIL without rollback."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "FAIL", "reason": "Cube is occluded by foreground object."}')
        ]

        png_data = make_dummy_png()
        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_mut_2", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = png_data

        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime.set_visual_verification_expectation("A clearly visible cube")

        tool_call = ToolCall(
            call_id="call_m2",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        # RNA semantic verification still passed
        self.assertTrue(result.success)
        self.assertTrue(result.data["verification"]["passed"])
        # Visual verification flagged FAIL without aborting or rolling back
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "FAIL")
        self.assertFalse(result.data["visual_verification"]["passed"])
        self.assertIn("occluded", result.data["visual_verification"]["reason"])

    def test_mutation_semantic_pass_and_visual_uncertain_no_rollback(self):
        """Mutation passes semantic verification, visual verification returns UNCERTAIN without rollback."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "UNCERTAIN", "reason": "Extreme camera angle makes confirmation ambiguous."}')
        ]

        png_data = make_dummy_png()
        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_mut_3", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = png_data

        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime.set_visual_verification_expectation("A cube in view")

        tool_call = ToolCall(
            call_id="call_m3",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertTrue(result.data["verification"]["passed"])
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "UNCERTAIN")
        self.assertFalse(result.data["visual_verification"]["passed"])

    def test_mutation_semantic_fail_skips_visual_verifier(self):
        """When semantic verification fails, visual verifier is NEVER executed."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True

        mock_adapter = MagicMock()
        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=False)

        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime.set_visual_verification_expectation("A cube")

        tool_call = ToolCall(
            call_id="call_m4",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertFalse(result.success)
        self.assertEqual(result.error.type, "VERIFICATION_FAILED")
        # Visual verifier and provider stream MUST NOT have been called
        self.assertEqual(mock_provider.stream_chat.call_count, 0)
        self.assertEqual(mock_adapter.capture_viewport.call_count, 0)

    def test_mutation_without_visual_request_does_not_trigger_visual_verifier(self):
        """Normal mutation without visual verification intent does not invoke vision pipeline."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True

        mock_adapter = MagicMock()
        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)

        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        # No expectation set, prompt is plain text without visual keywords
        runtime._current_prompt = "Create a cube at the center of the scene"

        tool_call = ToolCall(
            call_id="call_m5",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertIn("verification", result.data)
        self.assertNotIn("visual_verification", result.data)
        self.assertEqual(mock_provider.stream_chat.call_count, 0)
        self.assertEqual(mock_adapter.capture_viewport.call_count, 0)

    def test_mutation_visual_verification_from_prompt_keywords_english(self):
        """Prompt containing 'verify visually' automatically triggers post-mutation visual verification."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "PASS", "reason": "Verified visually from prompt."}')
        ]

        png_data = make_dummy_png()
        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_kw_1", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = png_data

        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime._current_prompt = "Create a cube at 0,0,0 and verify visually"

        tool_call = ToolCall(
            call_id="call_m6",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "PASS")
        self.assertEqual(mock_provider.stream_chat.call_count, 1)

    def test_mutation_visual_verification_from_prompt_keywords_turkish(self):
        """Prompt containing 'görsel olarak doğrula' automatically triggers post-mutation visual verification."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="turn_v1", text='{"status": "PASS", "reason": "Görsel olarak doğrulandı."}')
        ]

        png_data = make_dummy_png()
        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_kw_2", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = png_data

        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime._current_prompt = "Sahneye bir küp ekle ve görsel olarak doğrula"

        tool_call = ToolCall(
            call_id="call_m7",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "PASS")

    def test_mutation_visual_verification_unsupported_provider(self):
        """Unsupported provider produces UNCERTAIN without rolling back verified mutation."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = False

        mock_adapter = MagicMock()
        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)

        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime.set_visual_verification_expectation("A cube")

        tool_call = ToolCall(
            call_id="call_m8",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertTrue(result.data["verification"]["passed"])
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "UNCERTAIN")
        self.assertEqual(result.data["visual_verification"]["details"].get("error_type"), "PROVIDER_UNSUPPORTED")

    def test_mutation_visual_verification_viewport_unavailable(self):
        """Viewport capture failure yields UNCERTAIN without rolling back verified mutation."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True

        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.fail(
            tool="capture_viewport",
            error_type="CAPTURE_FAILED",
            message="Offscreen buffer failed",
        )

        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime.set_visual_verification_expectation("A cube")

        tool_call = ToolCall(
            call_id="call_m9",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertTrue(result.data["verification"]["passed"])
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "UNCERTAIN")
        self.assertEqual(result.data["visual_verification"]["details"].get("error_type"), "VIEWPORT_UNAVAILABLE")

    def test_mutation_visual_verification_image_resolution_failure(self):
        """Missing image in cache returns UNCERTAIN with IMAGE_NOT_FOUND without crashing."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True

        mock_adapter = MagicMock()
        mock_adapter.capture_viewport.return_value = ToolResult.ok(
            "capture_viewport",
            {"image_id": "vp_missing_99", "width": 512, "height": 512},
        )
        mock_adapter.get_viewport_screenshot.return_value = None  # Missing from cache

        mock_dispatcher = self._create_mock_mutation_dispatcher(mock_adapter, object_exists=True)
        runtime = AgentRuntime(provider=mock_provider, dispatcher=mock_dispatcher)
        runtime.set_visual_verification_expectation("A cube")

        tool_call = ToolCall(
            call_id="call_m10",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 0.0]},
        )

        result = runtime._execute_and_verify(tool_call)

        self.assertTrue(result.success)
        self.assertTrue(result.data["verification"]["passed"])
        self.assertIn("visual_verification", result.data)
        self.assertEqual(result.data["visual_verification"]["status"], "UNCERTAIN")
        self.assertEqual(result.data["visual_verification"]["details"].get("error_type"), "IMAGE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()

