"""Unit tests for M9 Task 3: CreateCameraTool, CameraMutator contract, and ChangeVerifier integration.

Pure Python test suite. Zero Blender (bpy) dependencies.
"""

import math
import unittest
from unittest.mock import MagicMock

from core.change_set import ChangeSet, VerificationStatus
from core.types import RiskLevel, ToolResult
from agent.dispatcher import ToolDispatcher
from agent.models import ToolCall
from agent.policy import ApprovalDecision, ApprovalPolicy
from agent.verifier import ChangeVerifier, build_change_set_from_result
from tools.mutations.create_camera import CreateCameraTool
from tools.registry import ToolRegistry


class TestCreateCameraToolContract(unittest.TestCase):
    """Test Tool contract, risk level, and JSON schema."""

    def setUp(self):
        self.tool = CreateCameraTool()

    def test_tool_metadata(self):
        self.assertEqual(self.tool.name, "create_camera")
        self.assertEqual(self.tool.risk_level, RiskLevel.LOW)
        self.assertIn("camera", self.tool.description.lower())

    def test_input_schema(self):
        schema = self.tool.input_schema
        self.assertEqual(schema.get("type"), "object")
        props = schema.get("properties", {})
        self.assertIn("name", props)
        self.assertIn("location", props)
        self.assertIn("rotation", props)
        self.assertIn("lens", props)
        self.assertIn("make_active", props)

    def test_registry_integration(self):
        registry = ToolRegistry()
        registry.register(self.tool)
        self.assertTrue(registry.exists("create_camera"))
        retrieved = registry.get("create_camera")
        self.assertIs(retrieved, self.tool)
        self.assertEqual(retrieved.risk_level, RiskLevel.LOW)


class TestCreateCameraToolExecution(unittest.TestCase):
    """Test execution via ToolDispatcher and mock BlenderAdapter."""

    def setUp(self):
        self.tool = CreateCameraTool()
        self.registry = ToolRegistry()
        self.registry.register(self.tool)
        self.mock_adapter = MagicMock()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.mock_adapter)

    def test_execute_with_defaults(self):
        expected_snapshot = {
            "created": True,
            "exists": True,
            "object_name": "Camera",
            "type": "CAMERA",
            "location": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "lens": 50.0,
            "is_active_camera": True,
        }
        self.mock_adapter.create_camera.return_value = ToolResult.ok(
            "create_camera",
            expected_snapshot,
        )

        call = ToolCall(call_id="call_1", tool_name="create_camera", arguments={})
        result = self.dispatcher.dispatch(call)
        self.assertTrue(result.success)
        self.mock_adapter.create_camera.assert_called_once_with(
            name=None,
            location=None,
            rotation=None,
            lens=None,
            make_active=True,
        )
        self.assertEqual(result.data["object_name"], "Camera")
        self.assertEqual(result.data["lens"], 50.0)
        self.assertTrue(result.data["is_active_camera"])

    def test_execute_with_custom_arguments(self):
        custom_snapshot = {
            "created": True,
            "exists": True,
            "object_name": "Main_Cam",
            "type": "CAMERA",
            "location": [0.0, -5.0, 2.0],
            "rotation": [1.1, 0.0, 0.0],
            "lens": 85.0,
            "is_active_camera": False,
        }
        self.mock_adapter.create_camera.return_value = ToolResult.ok(
            "create_camera",
            custom_snapshot,
        )

        call = ToolCall(
            call_id="call_2",
            tool_name="create_camera",
            arguments={
                "name": "Main_Cam",
                "location": [0.0, -5.0, 2.0],
                "rotation": [1.1, 0.0, 0.0],
                "lens": 85.0,
                "make_active": False,
            },
        )
        result = self.dispatcher.dispatch(call)
        self.assertTrue(result.success)
        self.mock_adapter.create_camera.assert_called_once_with(
            name="Main_Cam",
            location=[0.0, -5.0, 2.0],
            rotation=[1.1, 0.0, 0.0],
            lens=85.0,
            make_active=False,
        )
        self.assertEqual(result.data["object_name"], "Main_Cam")
        self.assertEqual(result.data["lens"], 85.0)
        self.assertFalse(result.data["is_active_camera"])

    def test_execute_adapter_error_fail_closed(self):
        self.mock_adapter.create_camera.return_value = ToolResult.fail(
            tool="create_camera",
            error_type="EXECUTION_FAILED",
            message="An object named 'Cube' already exists and is not a Camera.",
        )
        call = ToolCall(call_id="call_3", tool_name="create_camera", arguments={"name": "Cube"})
        result = self.dispatcher.dispatch(call)
        self.assertFalse(result.success)
        self.assertIn("already exists and is not a Camera", result.error.message)


class TestChangeVerifierCamera(unittest.TestCase):
    """Test ChangeVerifier logic specifically for create_camera operations."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_verify_create_camera_pass(self):
        cs = ChangeSet(
            operation="create_camera",
            target_name="Camera",
            expected_after={
                "type": "CAMERA",
                "location": [0.0, -5.0, 2.0],
                "rotation": [1.1, 0.0, 0.0],
                "lens": 50.0,
                "is_active_camera": True,
            },
            actual_after={
                "exists": True,
                "object_name": "Camera",
                "type": "CAMERA",
                "location": [0.0, -5.0, 2.0],
                "rotation": [1.1, 0.0, 0.0],
                "lens": 50.0,
                "is_active_camera": True,
            },
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.mismatches), 0)
        self.assertIn("PASSED", res.summary)

    def test_verify_create_camera_missing_object_fail(self):
        cs = ChangeSet(
            operation="create_camera",
            target_name="Camera",
            expected_after={"type": "CAMERA"},
            actual_after={"exists": False},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertFalse(res.passed)
        self.assertTrue(any(m["property"] == "exists" for m in res.mismatches))

    def test_verify_create_camera_type_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_camera",
            target_name="Camera",
            expected_after={"type": "CAMERA"},
            actual_after={"exists": True, "type": "MESH"},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "type" for m in res.mismatches))

    def test_verify_create_camera_location_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_camera",
            target_name="Camera",
            expected_after={"location": [0.0, 0.0, 0.0]},
            actual_after={"exists": True, "type": "CAMERA", "location": [0.0, 1.0, 0.0]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "location" for m in res.mismatches))

    def test_verify_create_camera_rotation_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_camera",
            target_name="Camera",
            expected_after={"rotation": [0.0, 0.0, 0.0]},
            actual_after={"exists": True, "type": "CAMERA", "rotation": [0.0, 0.0, math.pi / 2]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "rotation" for m in res.mismatches))

    def test_verify_create_camera_lens_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_camera",
            target_name="Camera",
            expected_after={"lens": 50.0},
            actual_after={"exists": True, "type": "CAMERA", "lens": 85.0},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "lens" for m in res.mismatches))

    def test_verify_create_camera_active_camera_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_camera",
            target_name="Camera",
            expected_after={"is_active_camera": True},
            actual_after={"exists": True, "type": "CAMERA", "is_active_camera": False},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "is_active_camera" for m in res.mismatches))

    def test_build_change_set_from_create_camera_result(self):
        result_data = {
            "created": True,
            "exists": True,
            "object_name": "KeyCam",
            "type": "CAMERA",
            "location": [1.0, 2.0, 3.0],
            "rotation": [0.0, 0.0, 1.57],
            "lens": 35.0,
            "is_active_camera": True,
        }
        arguments = {
            "name": "KeyCam",
            "location": [1.0, 2.0, 3.0],
            "rotation": [0.0, 0.0, 1.57],
            "lens": 35.0,
            "make_active": True,
        }
        cs = build_change_set_from_result("create_camera", arguments, result_data)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.operation, "create_camera")
        self.assertEqual(cs.target_name, "KeyCam")
        self.assertEqual(cs.expected_after.get("type"), "CAMERA")
        self.assertEqual(cs.expected_after.get("lens"), 35.0)
        self.assertTrue(cs.expected_after.get("is_active_camera"))

        # Verifier verification passes seamlessly with built changeset
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)


class TestApprovalPolicyForCamera(unittest.TestCase):
    """Test that CreateCameraTool integrates with ApprovalPolicy according to RiskLevel.LOW."""

    def setUp(self):
        self.tool = CreateCameraTool()
        self.policy = ApprovalPolicy()

    def test_low_risk_auto_approved(self):
        call = ToolCall(call_id="c1", tool_name="create_camera", arguments={})
        decision = self.policy.evaluate(self.tool, call)
        self.assertEqual(decision, ApprovalDecision.AUTO_APPROVE)

    def test_human_description_generation(self):
        desc = self.policy.create_human_description(
            "create_camera",
            {"name": "Studio_Cam", "lens": 85.0},
        )
        self.assertEqual(desc, 'Create camera "Studio_Cam"')

        desc_default = self.policy.create_human_description("create_camera", {})
        self.assertEqual(desc_default, 'Create camera')

    def test_pending_approval_creation(self):
        call = ToolCall(call_id="c2", tool_name="create_camera", arguments={"name": "Cam"})
        pending = self.policy.create_pending_approval("turn_1", self.tool, call)
        self.assertEqual(pending.tool_name, "create_camera")
        self.assertEqual(pending.risk_level, RiskLevel.LOW)
        self.assertEqual(pending.human_readable_description, 'Create camera "Cam"')


if __name__ == "__main__":
    unittest.main()
