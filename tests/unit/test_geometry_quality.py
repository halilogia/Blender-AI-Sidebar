"""Unit tests for M9 Task 5: Core Geometry Quality (set_shading & add_modifier).

Pure Python test suite. Zero Blender (bpy) dependencies.
"""

import unittest
from unittest.mock import MagicMock

from core.change_set import ChangeSet, VerificationStatus
from core.types import RiskLevel, ToolResult
from agent.dispatcher import ToolDispatcher
from agent.models import ToolCall
from agent.policy import ApprovalDecision, ApprovalPolicy
from agent.verifier import ChangeVerifier, build_change_set_from_result
from tools.mutations.set_shading import SetShadingTool
from tools.mutations.add_modifier import AddModifierTool
from tools.registry import ToolRegistry


class TestSetShadingContract(unittest.TestCase):
    """Test Tool contract and schema for SetShadingTool."""

    def setUp(self):
        self.tool = SetShadingTool()

    def test_metadata_and_schema(self):
        self.assertEqual(self.tool.name, "set_shading")
        self.assertEqual(self.tool.risk_level, RiskLevel.LOW)
        self.assertIn("shading", self.tool.description.lower())

        schema = self.tool.input_schema
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["name", "shading"])
        self.assertEqual(schema["properties"]["shading"]["enum"], ["SMOOTH", "FLAT"])

    def test_execution_via_dispatcher(self):
        registry = ToolRegistry()
        registry.register(self.tool)
        mock_adapter = MagicMock()
        dispatcher = ToolDispatcher(registry=registry, adapter=mock_adapter)

        mock_adapter.set_shading.return_value = ToolResult.ok(
            "set_shading",
            {"exists": True, "object_name": "Cube", "shading": "SMOOTH"},
        )

        call = ToolCall(call_id="c1", tool_name="set_shading", arguments={"name": "Cube", "shading": "SMOOTH"})
        res = dispatcher.dispatch(call)
        self.assertTrue(res.success)
        mock_adapter.set_shading.assert_called_once_with(name="Cube", shading="SMOOTH")


class TestAddModifierContractAndRisk(unittest.TestCase):
    """Test Tool contract, schema, and dynamic risk level for AddModifierTool."""

    def setUp(self):
        self.tool = AddModifierTool()

    def test_metadata_and_schema(self):
        self.assertEqual(self.tool.name, "add_modifier")
        self.assertEqual(self.tool.risk_level, RiskLevel.LOW)

        schema = self.tool.input_schema
        self.assertEqual(schema["required"], ["name", "modifier_type"])
        self.assertEqual(set(schema["properties"]["modifier_type"]["enum"]), {"BEVEL", "SUBSURF", "BOOLEAN"})

    def test_dynamic_risk_level(self):
        # BEVEL and SUBSURF are LOW risk
        self.assertEqual(self.tool.get_risk_level({"modifier_type": "BEVEL"}), RiskLevel.LOW)
        self.assertEqual(self.tool.get_risk_level({"modifier_type": "SUBSURF"}), RiskLevel.LOW)

        # BOOLEAN is MEDIUM risk (requires approval)
        self.assertEqual(self.tool.get_risk_level({"modifier_type": "BOOLEAN"}), RiskLevel.MEDIUM)

    def test_policy_evaluation_with_dynamic_risk(self):
        policy = ApprovalPolicy()

        # BEVEL -> AUTO_APPROVE
        call_bevel = ToolCall(call_id="c1", tool_name="add_modifier", arguments={"modifier_type": "BEVEL"})
        self.assertEqual(policy.evaluate(self.tool, call_bevel), ApprovalDecision.AUTO_APPROVE)

        # SUBSURF -> AUTO_APPROVE
        call_subsurf = ToolCall(call_id="c2", tool_name="add_modifier", arguments={"modifier_type": "SUBSURF"})
        self.assertEqual(policy.evaluate(self.tool, call_subsurf), ApprovalDecision.AUTO_APPROVE)

        # BOOLEAN -> REQUIRE_APPROVAL
        call_bool = ToolCall(call_id="c3", tool_name="add_modifier", arguments={"modifier_type": "BOOLEAN"})
        self.assertEqual(policy.evaluate(self.tool, call_bool), ApprovalDecision.REQUIRE_APPROVAL)

        # Pending approval reflects MEDIUM risk for Boolean
        pending = policy.create_pending_approval("turn_1", self.tool, call_bool)
        self.assertEqual(pending.risk_level, RiskLevel.MEDIUM)

    def test_execution_via_dispatcher(self):
        registry = ToolRegistry()
        registry.register(self.tool)
        mock_adapter = MagicMock()
        dispatcher = ToolDispatcher(registry=registry, adapter=mock_adapter)

        mock_adapter.add_modifier.return_value = ToolResult.ok(
            "add_modifier",
            {"exists": True, "object_name": "Cube", "modifier_name": "Bevel", "modifier_type": "BEVEL"},
        )

        call = ToolCall(call_id="c1", tool_name="add_modifier", arguments={"name": "Cube", "modifier_type": "BEVEL", "width": 0.02})
        res = dispatcher.dispatch(call)
        self.assertTrue(res.success)
        mock_adapter.add_modifier.assert_called_once_with(
            name="Cube",
            modifier_type="BEVEL",
            modifier_name=None,
            width=0.02,
            segments=None,
            levels=None,
            operation=None,
            target_object=None,
        )


class TestChangeVerifierGeometryQuality(unittest.TestCase):
    """Test ChangeVerifier rules for set_shading and add_modifier."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    # 1. set_shading verification
    def test_verify_set_shading_pass(self):
        cs = ChangeSet(
            operation="set_shading",
            target_name="Sphere",
            expected_after={"shading": "SMOOTH"},
            actual_after={"exists": True, "shading": "SMOOTH"},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertTrue(res.passed)

    def test_verify_set_shading_mismatch(self):
        cs = ChangeSet(
            operation="set_shading",
            target_name="Sphere",
            expected_after={"shading": "SMOOTH"},
            actual_after={"exists": True, "shading": "FLAT"},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertFalse(res.passed)
        self.assertEqual(res.mismatches[0]["property"], "shading")

    # 2. add_modifier: BEVEL verification
    def test_verify_add_modifier_bevel_pass(self):
        cs = ChangeSet(
            operation="add_modifier",
            target_name="Cube",
            expected_after={"modifier_type": "BEVEL", "width": 0.05, "segments": 3},
            actual_after={"exists": True, "modifier_type": "BEVEL", "width": 0.05, "segments": 3},
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)

    def test_verify_add_modifier_bevel_width_mismatch(self):
        cs = ChangeSet(
            operation="add_modifier",
            target_name="Cube",
            expected_after={"modifier_type": "BEVEL", "width": 0.05, "segments": 3},
            actual_after={"exists": True, "modifier_type": "BEVEL", "width": 0.1, "segments": 3},
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.mismatches[0]["property"], "width")

    # 3. add_modifier: SUBSURF verification
    def test_verify_add_modifier_subsurf_pass(self):
        cs = ChangeSet(
            operation="add_modifier",
            target_name="Cube",
            expected_after={"modifier_type": "SUBSURF", "levels": 2},
            actual_after={"exists": True, "modifier_type": "SUBSURF", "levels": 2},
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)

    def test_verify_add_modifier_subsurf_levels_mismatch(self):
        cs = ChangeSet(
            operation="add_modifier",
            target_name="Cube",
            expected_after={"modifier_type": "SUBSURF", "levels": 2},
            actual_after={"exists": True, "modifier_type": "SUBSURF", "levels": 1},
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.mismatches[0]["property"], "levels")

    # 4. add_modifier: BOOLEAN verification
    def test_verify_add_modifier_boolean_pass(self):
        cs = ChangeSet(
            operation="add_modifier",
            target_name="Wall",
            expected_after={"modifier_type": "BOOLEAN", "operation": "DIFFERENCE", "target_object": "WindowHole"},
            actual_after={"exists": True, "modifier_type": "BOOLEAN", "operation": "DIFFERENCE", "target_object": "WindowHole"},
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)

    def test_verify_add_modifier_boolean_target_mismatch(self):
        cs = ChangeSet(
            operation="add_modifier",
            target_name="Wall",
            expected_after={"modifier_type": "BOOLEAN", "operation": "DIFFERENCE", "target_object": "WindowHole"},
            actual_after={"exists": True, "modifier_type": "BOOLEAN", "operation": "DIFFERENCE", "target_object": "OtherObj"},
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.mismatches[0]["property"], "target_object")

    # 5. build_change_set_from_result mapper
    def test_build_change_set_from_result_shading(self):
        args = {"name": "Cylinder", "shading": "SMOOTH"}
        data = {"exists": True, "object_name": "Cylinder", "shading": "SMOOTH"}
        cs = build_change_set_from_result("set_shading", args, data)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.operation, "set_shading")
        self.assertEqual(cs.expected_after["shading"], "SMOOTH")

    def test_build_change_set_from_result_modifier(self):
        args = {"name": "Cube", "modifier_type": "BEVEL", "width": 0.03, "segments": 4}
        data = {"exists": True, "object_name": "Cube", "modifier_type": "BEVEL", "width": 0.03, "segments": 4}
        cs = build_change_set_from_result("add_modifier", args, data)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.operation, "add_modifier")
        self.assertEqual(cs.expected_after["modifier_type"], "BEVEL")
        self.assertEqual(cs.expected_after["width"], 0.03)
        self.assertEqual(cs.expected_after["segments"], 4)


class TestApprovalDescriptions(unittest.TestCase):
    """Test human-readable description generation for shading and modifier tools."""

    def setUp(self):
        self.policy = ApprovalPolicy()

    def test_set_shading_description(self):
        desc = self.policy.create_human_description("set_shading", {"name": "Suzanne", "shading": "SMOOTH"})
        self.assertEqual(desc, 'Set smooth shading on "Suzanne"')

    def test_add_modifier_description(self):
        desc = self.policy.create_human_description("add_modifier", {"name": "Cube", "modifier_type": "BEVEL"})
        self.assertEqual(desc, 'Add Bevel modifier to "Cube"')


if __name__ == "__main__":
    unittest.main()
