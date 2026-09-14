"""Unit tests for M9 Task 4: CreateLightTool, LightMutator contract, and ChangeVerifier integration.

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
from tools.mutations.create_light import CreateLightTool
from tools.registry import ToolRegistry


class TestCreateLightToolContract(unittest.TestCase):
    """Test Tool contract, risk level, and JSON schema."""

    def setUp(self):
        self.tool = CreateLightTool()

    def test_tool_metadata(self):
        self.assertEqual(self.tool.name, "create_light")
        self.assertEqual(self.tool.risk_level, RiskLevel.LOW)
        self.assertIn("light", self.tool.description.lower())

    def test_input_schema(self):
        schema = self.tool.input_schema
        self.assertEqual(schema.get("type"), "object")
        props = schema.get("properties", {})
        self.assertIn("name", props)
        self.assertIn("light_type", props)
        self.assertIn("location", props)
        self.assertIn("rotation", props)
        self.assertIn("energy", props)
        self.assertIn("color", props)

        # Light types enum
        enum_types = props["light_type"].get("enum", [])
        self.assertEqual(set(enum_types), {"POINT", "SUN", "SPOT", "AREA"})

    def test_registry_integration(self):
        registry = ToolRegistry()
        registry.register(self.tool)
        self.assertTrue(registry.exists("create_light"))
        retrieved = registry.get("create_light")
        self.assertIs(retrieved, self.tool)
        self.assertEqual(retrieved.risk_level, RiskLevel.LOW)


class TestCreateLightToolExecution(unittest.TestCase):
    """Test execution via ToolDispatcher and mock BlenderAdapter."""

    def setUp(self):
        self.tool = CreateLightTool()
        self.registry = ToolRegistry()
        self.registry.register(self.tool)
        self.mock_adapter = MagicMock()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.mock_adapter)

    def test_execute_with_defaults(self):
        expected_snapshot = {
            "created": True,
            "exists": True,
            "object_name": "Light",
            "type": "LIGHT",
            "light_type": "POINT",
            "location": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "energy": 10.0,
            "color": [1.0, 1.0, 1.0],
        }
        self.mock_adapter.create_light.return_value = ToolResult.ok(
            "create_light",
            expected_snapshot,
        )

        call = ToolCall(call_id="call_l1", tool_name="create_light", arguments={})
        result = self.dispatcher.dispatch(call)
        self.assertTrue(result.success)
        self.mock_adapter.create_light.assert_called_once_with(
            name=None,
            light_type=None,
            location=None,
            rotation=None,
            energy=None,
            color=None,
        )
        self.assertEqual(result.data["object_name"], "Light")
        self.assertEqual(result.data["light_type"], "POINT")
        self.assertEqual(result.data["energy"], 10.0)

    def test_execute_with_custom_arguments(self):
        custom_snapshot = {
            "created": True,
            "exists": True,
            "object_name": "KeyLight",
            "type": "LIGHT",
            "light_type": "SUN",
            "location": [5.0, -5.0, 10.0],
            "rotation": [0.78, 0.0, 0.5],
            "energy": 500.0,
            "color": [1.0, 0.95, 0.8],
        }
        self.mock_adapter.create_light.return_value = ToolResult.ok(
            "create_light",
            custom_snapshot,
        )

        call = ToolCall(
            call_id="call_l2",
            tool_name="create_light",
            arguments={
                "name": "KeyLight",
                "light_type": "SUN",
                "location": [5.0, -5.0, 10.0],
                "rotation": [0.78, 0.0, 0.5],
                "energy": 500.0,
                "color": [1.0, 0.95, 0.8],
            },
        )
        result = self.dispatcher.dispatch(call)
        self.assertTrue(result.success)
        self.mock_adapter.create_light.assert_called_once_with(
            name="KeyLight",
            light_type="SUN",
            location=[5.0, -5.0, 10.0],
            rotation=[0.78, 0.0, 0.5],
            energy=500.0,
            color=[1.0, 0.95, 0.8],
        )
        self.assertEqual(result.data["object_name"], "KeyLight")
        self.assertEqual(result.data["light_type"], "SUN")
        self.assertEqual(result.data["energy"], 500.0)

    def test_execute_adapter_error_fail_closed(self):
        self.mock_adapter.create_light.return_value = ToolResult.fail(
            tool="create_light",
            error_type="EXECUTION_FAILED",
            message="Object 'Cube' already exists but is of type 'MESH', not 'LIGHT'.",
        )
        call = ToolCall(call_id="call_l3", tool_name="create_light", arguments={"name": "Cube"})
        result = self.dispatcher.dispatch(call)
        self.assertFalse(result.success)
        self.assertIn("already exists but is of type 'MESH'", result.error.message)


class TestChangeVerifierLight(unittest.TestCase):
    """Test ChangeVerifier logic specifically for create_light operations."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_verify_create_light_pass(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Sun_Light",
            expected_after={
                "type": "LIGHT",
                "light_type": "SUN",
                "location": [0.0, 0.0, 5.0],
                "rotation": [0.5, 0.0, 0.0],
                "energy": 100.0,
                "color": [1.0, 1.0, 0.9],
            },
            actual_after={
                "exists": True,
                "object_name": "Sun_Light",
                "type": "LIGHT",
                "light_type": "SUN",
                "location": [0.0, 0.0, 5.0],
                "rotation": [0.5, 0.0, 0.0],
                "energy": 100.0,
                "color": [1.0, 1.0, 0.9],
            },
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.mismatches), 0)
        self.assertIn("PASSED", res.summary)

    def test_verify_create_light_missing_object_fail(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Light",
            expected_after={"type": "LIGHT"},
            actual_after={"exists": False},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertFalse(res.passed)
        self.assertTrue(any(m["property"] == "exists" for m in res.mismatches))

    def test_verify_create_light_type_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Light",
            expected_after={"type": "LIGHT"},
            actual_after={"exists": True, "type": "MESH"},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "type" for m in res.mismatches))

    def test_verify_create_light_light_type_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Light",
            expected_after={"light_type": "SPOT"},
            actual_after={"exists": True, "type": "LIGHT", "light_type": "POINT"},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "light_type" for m in res.mismatches))

    def test_verify_create_light_location_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Light",
            expected_after={"location": [0.0, 0.0, 1.0]},
            actual_after={"exists": True, "type": "LIGHT", "location": [0.0, 0.0, 2.0]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "location" for m in res.mismatches))

    def test_verify_create_light_rotation_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Light",
            expected_after={"rotation": [0.0, 0.0, 0.0]},
            actual_after={"exists": True, "type": "LIGHT", "rotation": [0.0, 0.0, math.pi / 2]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "rotation" for m in res.mismatches))

    def test_verify_create_light_energy_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Light",
            expected_after={"energy": 100.0},
            actual_after={"exists": True, "type": "LIGHT", "energy": 10.0},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "energy" for m in res.mismatches))

    def test_verify_create_light_color_mismatch_fail(self):
        cs = ChangeSet(
            operation="create_light",
            target_name="Light",
            expected_after={"color": [1.0, 1.0, 1.0]},
            actual_after={"exists": True, "type": "LIGHT", "color": [1.0, 0.0, 0.0]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertTrue(any(m["property"] == "color" for m in res.mismatches))

    def test_build_change_set_from_create_light_result(self):
        result_data = {
            "created": True,
            "exists": True,
            "object_name": "StudioSpot",
            "type": "LIGHT",
            "light_type": "SPOT",
            "location": [2.0, 3.0, 5.0],
            "rotation": [0.2, 0.4, 0.0],
            "energy": 250.0,
            "color": [0.9, 0.95, 1.0],
        }
        arguments = {
            "name": "StudioSpot",
            "light_type": "SPOT",
            "location": [2.0, 3.0, 5.0],
            "rotation": [0.2, 0.4, 0.0],
            "energy": 250.0,
            "color": [0.9, 0.95, 1.0],
        }
        cs = build_change_set_from_result("create_light", arguments, result_data)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.operation, "create_light")
        self.assertEqual(cs.target_name, "StudioSpot")
        self.assertEqual(cs.expected_after.get("type"), "LIGHT")
        self.assertEqual(cs.expected_after.get("light_type"), "SPOT")
        self.assertEqual(cs.expected_after.get("energy"), 250.0)
        self.assertEqual(cs.expected_after.get("color"), [0.9, 0.95, 1.0])

        # Verifier verification passes seamlessly with built changeset
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)


class TestApprovalPolicyForLight(unittest.TestCase):
    """Test that CreateLightTool integrates with ApprovalPolicy according to RiskLevel.LOW."""

    def setUp(self):
        self.tool = CreateLightTool()
        self.policy = ApprovalPolicy()

    def test_low_risk_auto_approved(self):
        call = ToolCall(call_id="c1", tool_name="create_light", arguments={})
        decision = self.policy.evaluate(self.tool, call)
        self.assertEqual(decision, ApprovalDecision.AUTO_APPROVE)

    def test_human_description_generation(self):
        desc = self.policy.create_human_description(
            "create_light",
            {"name": "SunLight", "light_type": "SUN"},
        )
        self.assertEqual(desc, 'Create sun "SunLight"')

        desc_default = self.policy.create_human_description("create_light", {})
        self.assertEqual(desc_default, 'Create light')

    def test_pending_approval_creation(self):
        call = ToolCall(call_id="c2", tool_name="create_light", arguments={"name": "FillLight"})
        pending = self.policy.create_pending_approval("turn_1", self.tool, call)
        self.assertEqual(pending.tool_name, "create_light")
        self.assertEqual(pending.risk_level, RiskLevel.LOW)
        self.assertEqual(pending.human_readable_description, 'Create light "FillLight"')


if __name__ == "__main__":
    unittest.main()
