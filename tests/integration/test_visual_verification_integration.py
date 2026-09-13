"""Integration and acceptance tests for M7 Task 3: Visual Scene Verification in Blender 5.2.1.

Verifies:
1. Real Blender mutation -> Semantic verification PASS -> Viewport capture -> Visual verification PASS.
2. Semantic verification PASS + Visual FAIL: reports visual discrepancy WITHOUT rolling back valid Blender state.
3. VisualVerifyTool execution via ToolDispatcher on real 3D Viewport.
4. Thread safety: background threads cannot execute capture_viewport or get_viewport_screenshot.
5. Deterministic ImageResolutionError on missing image cache reference.
6. Privacy & hygiene: Zero raw PNG bytes or base64 strings in data structures or logs.
"""

import json
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from agent.context_builder import ImageResolutionError
from agent.dispatcher import ToolDispatcher
from agent.models import TextDelta, ToolCall
from agent.runtime import AgentRuntime
from agent.verifier import ChangeVerifier, build_change_set_from_result
from agent.visual_verifier import (
    VisualVerificationResult,
    VisualVerificationStatus,
    VisualVerifier,
)
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.set_material import SetMaterialTool
from tools.read_only.capture_viewport import CaptureViewportTool
from tools.read_only.visual_verify import VisualVerifyTool
from tools.registry import ToolRegistry


class TestVisualVerificationIntegration(unittest.TestCase):
    """Headless integration tests for visual verification with Blender 5.2.1."""

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=False)
        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(SetMaterialTool())
        self.registry.register(CaptureViewportTool())
        self.registry.register(VisualVerifyTool())

        self.adapter = BlenderAdapter()
        self.dispatcher = ToolDispatcher(self.registry, self.adapter)
        self.verifier = ChangeVerifier()

    def test_01_real_mutation_semantic_pass_and_visual_pass(self):
        """Mutation passes semantic verification, then AgentRuntime automatically runs visual verification (PASS)."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="vis_turn_1", text='{"status": "PASS", "reason": "VisualTestCube is clearly visible at origin."}')
        ]

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
        )
        runtime.set_visual_verification_expectation("A cube named VisualTestCube at the world origin")

        create_call = ToolCall(
            call_id="call_c1",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "name": "VisualTestCube", "location": [0.0, 0.0, 0.0]},
        )

        tool_res = runtime._execute_and_verify(create_call)
        self.assertTrue(tool_res.success)

        # 1. Semantic verification passed
        self.assertIn("verification", tool_res.data)
        self.assertTrue(tool_res.data["verification"]["passed"])

        # 2. Visual verification was automatically executed and passed
        self.assertIn("visual_verification", tool_res.data)
        self.assertEqual(tool_res.data["visual_verification"]["status"], "PASS")
        self.assertTrue(tool_res.data["visual_verification"]["passed"])
        self.assertIn("VisualTestCube", tool_res.data["visual_verification"]["reason"])

        # 3. Provider was called with real viewport image
        self.assertEqual(mock_provider.stream_chat.call_count, 1)
        image_id = tool_res.data["visual_verification"]["image_id"]
        self.assertIsNotNone(image_id)
        self.assertTrue(image_id.startswith("vp_"))

        # Verify real PNG bytes exist in adapter in-memory cache
        png_bytes = self.adapter.get_viewport_screenshot(image_id)
        self.assertIsNotNone(png_bytes)
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_02_semantic_pass_and_visual_fail_does_not_rollback(self):
        """Visual FAIL signals visual discrepancy without reverting valid Blender RNA state."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="vis_turn_2", text='{"status": "FAIL", "reason": "Material appears black due to lack of scene lighting."}')
        ]

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
        )
        runtime.set_visual_verification_expectation("Bright red material on Cube")

        call = ToolCall(
            call_id="call_mat_1",
            tool_name="set_material",
            arguments={"object_name": "Cube", "base_color": [0.8, 0.1, 0.1, 1.0]},
        )

        tool_res = runtime._execute_and_verify(call)
        self.assertTrue(tool_res.success)

        # 1. Semantic verification passed
        self.assertTrue(tool_res.data["verification"]["passed"])

        # 2. Visual verification flagged FAIL without aborting or rolling back
        self.assertIn("visual_verification", tool_res.data)
        self.assertEqual(tool_res.data["visual_verification"]["status"], "FAIL")
        self.assertFalse(tool_res.data["visual_verification"]["passed"])
        self.assertIn("lack of scene lighting", tool_res.data["visual_verification"]["reason"])

        # 3. Assert Blender RNA state was NOT rolled back
        obj = bpy.data.objects.get("Cube")
        self.assertIsNotNone(obj)
        mat = obj.active_material
        self.assertIsNotNone(mat)
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        # Base color remains 0.8 red
        self.assertAlmostEqual(bsdf.inputs["Base Color"].default_value[0], 0.8, places=3)

    def test_03_runtime_mutation_semantic_pass_and_visual_uncertain_no_rollback(self):
        """Visual UNCERTAIN does not revert valid Blender RNA state."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="vis_turn_3", text='{"status": "UNCERTAIN", "reason": "Camera viewing angle does not show Cube clearly."}')
        ]

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
        )
        runtime.set_visual_verification_expectation("Cube in active viewport")

        call = ToolCall(
            call_id="call_c3",
            tool_name="create_primitive",
            arguments={"primitive_type": "SPHERE", "name": "VisualUncertainSphere", "location": [1.0, 1.0, 1.0]},
        )

        tool_res = runtime._execute_and_verify(call)
        self.assertTrue(tool_res.success)
        self.assertTrue(tool_res.data["verification"]["passed"])
        self.assertIn("visual_verification", tool_res.data)
        self.assertEqual(tool_res.data["visual_verification"]["status"], "UNCERTAIN")
        self.assertFalse(tool_res.data["visual_verification"]["passed"])

        # Live Blender state remains intact
        obj = bpy.data.objects.get("VisualUncertainSphere")
        self.assertIsNotNone(obj)

    def test_04_runtime_mutation_semantic_fail_skips_visual_verifier(self):
        """When semantic verification fails, visual verifier is NEVER executed."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
        )
        runtime.set_visual_verification_expectation("A nonexistent object")

        call = ToolCall(
            call_id="call_f1",
            tool_name="set_material",
            arguments={"object_name": "NonExistentObjectXYZ", "base_color": [0.0, 1.0, 0.0, 1.0]},
        )

        tool_res = runtime._execute_and_verify(call)
        self.assertFalse(tool_res.success)
        self.assertEqual(mock_provider.stream_chat.call_count, 0)

    def test_05_runtime_mutation_without_visual_request_does_not_call_visual_verifier(self):
        """Mutation without visual request only runs semantic verification (regression guard)."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
        )
        # Plain prompt without visual keywords
        runtime._current_prompt = "Create a plane at origin"

        call = ToolCall(
            call_id="call_p1",
            tool_name="create_primitive",
            arguments={"primitive_type": "PLANE", "name": "PlainPlane", "location": [0.0, 0.0, 0.0]},
        )

        tool_res = runtime._execute_and_verify(call)
        self.assertTrue(tool_res.success)
        self.assertIn("verification", tool_res.data)
        self.assertTrue(tool_res.data["verification"]["passed"])
        self.assertNotIn("visual_verification", tool_res.data)
        self.assertEqual(mock_provider.stream_chat.call_count, 0)

    def test_06_visual_verify_tool_execution(self):
        """VisualVerifyTool captures active viewport and integrates with AgentRuntime."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="vis_turn_6", text='{"status": "PASS", "reason": "3D scene confirmed."}')
        ]

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
        )

        tool_call = ToolCall(
            call_id="call_v_real",
            tool_name="visual_verify",
            arguments={"expected_description": "Default Blender scene with Cube"},
        )

        res = runtime._execute_and_verify(tool_call)
        self.assertTrue(res.success)
        self.assertIn("visual_verification", res.data)
        self.assertEqual(res.data["visual_verification"]["status"], "PASS")
        self.assertIsNotNone(res.data["image_id"])

    def test_07_thread_safety_background_thread_cannot_capture(self):
        """Background thread invoking capture_viewport raises ThreadSafetyViolationError."""
        captured_error = []

        def bg_worker():
            try:
                self.adapter.capture_viewport()
            except ThreadSafetyViolationError as exc:
                captured_error.append(exc)

        t = threading.Thread(target=bg_worker)
        t.start()
        t.join(timeout=1.0)

        self.assertEqual(len(captured_error), 1)
        self.assertIn("main thread", str(captured_error[0]).lower())

    def test_08_missing_image_cache_deterministic_error(self):
        """Referencing a missing or expired image_id raises ImageResolutionError."""
        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True

        visual_verifier = VisualVerifier(
            provider=mock_provider,
            adapter=self.adapter,
        )

        with self.assertRaises(ImageResolutionError) as cm:
            visual_verifier.verify("Test expectation", image_id="vp_nonexistent_xyz")
        self.assertEqual(cm.exception.image_id, "vp_nonexistent_xyz")

    def test_09_hygiene_no_raw_bytes_in_serialized_result(self):
        """VisualVerificationResult.to_dict() never leaks raw bytes or base64."""
        cap_res = self.adapter.capture_viewport()
        self.assertTrue(cap_res.success)
        image_id = cap_res.data["image_id"]

        mock_provider = MagicMock()
        mock_provider.supports_multimodal = True
        mock_provider.stream_chat.return_value = [
            TextDelta(turn_id="vis_turn_9", text='{"status": "PASS", "reason": "Clean scene."}')
        ]

        visual_verifier = VisualVerifier(provider=mock_provider, adapter=self.adapter)
        vis_res = visual_verifier.verify("Clean scene", image_id=image_id)

        d = vis_res.to_dict()
        serialized = json.dumps(d)
        self.assertNotIn("base64", serialized)
        self.assertNotIn("\x89PNG", serialized)
        self.assertEqual(d["image_id"], image_id)


def run():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestVisualVerificationIntegration)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run())
