"""Integration and acceptance suite for M7 Task 1: Viewport Screenshot Capture in Blender 5.2.1.

Verifies:
1. Tool registration in ToolRegistry.
2. RiskLevel.READ_ONLY classification.
3. Thread safety enforcement (ThreadSafetyViolationError on background thread).
4. Real headless GPU offscreen rendering and PNG capture.
5. Screenshot not empty/null with valid PNG binary signature.
6. Machine-readable metadata (width, height, format, mime_type, byte_size).
7. Zero scene mutation verification (objects, meshes, materials, images, selection).
8. End-to-end dispatch via ToolDispatcher.
9. Dimension boundary validation.
"""

import os
import sys
import threading
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from agent.dispatcher import ToolDispatcher
from agent.models import ToolCall
from core.types import RiskLevel
from tools.read_only.capture_viewport import CaptureViewportTool
from tools.registry import ToolRegistry


class TestViewportCaptureIntegration(unittest.TestCase):
    """Headless integration tests for viewport capture."""

    def setUp(self):
        # Factory reset homefile to clean baseline
        bpy.ops.wm.read_homefile(use_empty=False)

        self.registry = ToolRegistry()
        self.tool = CaptureViewportTool()
        self.registry.register(self.tool)

        self.adapter = BlenderAdapter()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.adapter)

    def test_01_tool_registration_and_risk(self):
        """Tool is registered in ToolRegistry and classified as READ_ONLY."""
        self.assertTrue(self.registry.exists("capture_viewport"))
        retrieved_tool = self.registry.get("capture_viewport")
        self.assertEqual(retrieved_tool.risk_level, RiskLevel.READ_ONLY)
        self.assertEqual(retrieved_tool.name, "capture_viewport")

    def test_02_thread_safety_enforcement(self):
        """Calling capture_viewport from background worker thread raises ThreadSafetyViolationError."""
        caught_error = False

        def background_worker():
            nonlocal caught_error
            try:
                self.adapter.capture_viewport(width=128, height=128)
            except ThreadSafetyViolationError:
                caught_error = True

        thread = threading.Thread(target=background_worker)
        thread.start()
        thread.join()

        self.assertTrue(caught_error, "ThreadSafetyViolationError was not raised from background thread.")

    def test_03_real_headless_viewport_capture(self):
        """Capture live viewport in headless Blender and verify non-empty PNG payload."""
        res = self.adapter.capture_viewport(width=256, height=256)
        self.assertTrue(res.success, f"capture_viewport failed: {res.error}")

        data = res.data
        self.assertIn("image_id", data)
        self.assertEqual(data["width"], 256)
        self.assertEqual(data["height"], 256)
        self.assertEqual(data["format"], "PNG")
        self.assertEqual(data["mime_type"], "image/png")
        self.assertGreater(data["byte_size"], 0)
        self.assertEqual(data["channels"], 4)

        # Retrieve raw PNG bytes from in-memory cache
        png_bytes = self.adapter.get_viewport_screenshot(data["image_id"])
        self.assertIsNotNone(png_bytes)
        self.assertEqual(len(png_bytes), data["byte_size"])
        self.assertTrue(
            png_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
            "Generated image does not start with valid 8-byte PNG header.",
        )

    def test_04_zero_scene_mutation(self):
        """Viewport capture causes zero scene, object, material, image, or selection contamination."""
        def snapshot_state():
            return {
                "objects": [obj.name for obj in bpy.data.objects],
                "meshes": [m.name for m in bpy.data.meshes],
                "materials": [mat.name for mat in bpy.data.materials],
                "images": [img.name for img in bpy.data.images],
                "active_object": bpy.context.active_object.name if bpy.context.active_object else None,
                "selected_objects": [obj.name for obj in bpy.context.selected_objects],
            }

        pre_state = snapshot_state()

        # Perform multiple captures with different sizes
        res1 = self.adapter.capture_viewport(width=128, height=128)
        self.assertTrue(res1.success)

        res2 = self.adapter.capture_viewport(width=320, height=240)
        self.assertTrue(res2.success)

        post_state = snapshot_state()
        self.assertEqual(pre_state, post_state, f"Scene mutated! Pre: {pre_state} vs Post: {post_state}")

    def test_05_dispatcher_execution_roundtrip(self):
        """capture_viewport dispatches cleanly through ToolDispatcher."""
        tc = ToolCall(
            call_id="call_vp_1",
            tool_name="capture_viewport",
            arguments={"width": 128, "height": 128},
        )
        res = self.dispatcher.dispatch(tc)
        self.assertTrue(res.success, f"Dispatcher failed: {res.error}")
        self.assertEqual(res.data["width"], 128)
        self.assertEqual(res.data["height"], 128)
        self.assertEqual(res.data["format"], "PNG")
        self.assertIn("image_id", res.data)

    def test_06_dimension_validation(self):
        """Invalid or out-of-bounds dimensions return INVALID_ARGUMENT failure."""
        res_neg = self.adapter.capture_viewport(width=-10, height=256)
        self.assertFalse(res_neg.success)
        self.assertEqual(res_neg.error.type, "INVALID_ARGUMENT")

        res_zero = self.adapter.capture_viewport(width=256, height=0)
        self.assertFalse(res_zero.success)
        self.assertEqual(res_zero.error.type, "INVALID_ARGUMENT")

        res_huge = self.adapter.capture_viewport(width=5000, height=5000)
        self.assertFalse(res_huge.success)
        self.assertEqual(res_huge.error.type, "INVALID_ARGUMENT")


def run_tests():
    print("\n========================================================")
    print("   RUNNING M7 TASK 1 VIEWPORT CAPTURE INTEGRATION SUITE ")
    print("========================================================\n")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestViewportCaptureIntegration)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = run_tests()
    sys.exit(code)
