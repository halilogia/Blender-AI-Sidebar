"""Unit tests for M9 Task 6: duplicate_object semantic tool.

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
from tools.mutations.duplicate_object import DuplicateObjectTool
from tools.registry import ToolRegistry


class TestDuplicateObjectContract(unittest.TestCase):
    """Test Tool contract, schema, and execution delegation for DuplicateObjectTool."""

    def setUp(self):
        self.tool = DuplicateObjectTool()

    def test_metadata_and_schema(self):
        self.assertEqual(self.tool.name, "duplicate_object")
        self.assertEqual(self.tool.risk_level, RiskLevel.LOW)
        self.assertIn("duplicate", self.tool.description.lower())

        schema = self.tool.input_schema
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["source_name"])
        self.assertFalse(schema.get("additionalProperties", True))
        self.assertIn("source_name", schema["properties"])
        self.assertIn("new_name", schema["properties"])
        self.assertIn("location", schema["properties"])
        self.assertIn("rotation", schema["properties"])
        self.assertIn("scale", schema["properties"])

    def test_execution_via_dispatcher(self):
        registry = ToolRegistry()
        registry.register(self.tool)
        mock_adapter = MagicMock()
        dispatcher = ToolDispatcher(registry=registry, adapter=mock_adapter)

        mock_adapter.duplicate_object.return_value = ToolResult.ok(
            "duplicate_object",
            {
                "duplicated": True,
                "source_name": "Chair",
                "new_name": "Chair_02",
                "source_exists": True,
                "new_exists": True,
                "type": "MESH",
                "location": [1.0, 2.0, 3.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "distinct_identity": True,
            },
        )

        call = ToolCall(
            call_id="c_dup_1",
            tool_name="duplicate_object",
            arguments={
                "source_name": "Chair",
                "new_name": "Chair_02",
                "location": [1.0, 2.0, 3.0],
            },
        )
        res = dispatcher.dispatch(call)
        self.assertTrue(res.success)
        mock_adapter.duplicate_object.assert_called_once_with(
            source_name="Chair",
            new_name="Chair_02",
            location=[1.0, 2.0, 3.0],
            rotation=None,
            scale=None,
        )


class TestDuplicateObjectVerification(unittest.TestCase):
    """Test deterministic verification rules for duplicate_object in ChangeVerifier."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_verifier_passes_on_valid_duplication(self):
        cs = ChangeSet(
            operation="duplicate_object",
            target_name="Chair_02",
            before={"location": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
            expected_after={
                "source_name": "Chair",
                "source_exists": True,
                "new_exists": True,
                "new_name": "Chair_02",
                "type": "MESH",
                "location": [2.0, 0.0, 0.0],
                "distinct_identity": True,
            },
            actual_after={
                "source_name": "Chair",
                "new_name": "Chair_02",
                "source_exists": True,
                "new_exists": True,
                "type": "MESH",
                "location": [2.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "source_location": [0.0, 0.0, 0.0],
                "source_rotation": [0.0, 0.0, 0.0],
                "source_scale": [1.0, 1.0, 1.0],
                "distinct_identity": True,
                "source_preserved": True,
            },
        )
        result = self.verifier.verify(cs)
        self.assertEqual(result.status, VerificationStatus.PASS)
        self.assertEqual(len(result.mismatches), 0)

    def test_verifier_fails_when_source_not_preserved(self):
        cs = ChangeSet(
            operation="duplicate_object",
            target_name="Chair_02",
            before={"location": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
            expected_after={"source_name": "Chair", "source_exists": True, "new_exists": True},
            actual_after={
                "source_name": "Chair",
                "new_name": "Chair_02",
                "source_exists": True,
                "new_exists": True,
                "distinct_identity": True,
                "source_preserved": False,
            },
        )
        result = self.verifier.verify(cs)
        self.assertEqual(result.status, VerificationStatus.FAIL)
        mismatch_props = [m["property"] for m in result.mismatches]
        self.assertIn("source_preserved", mismatch_props)

    def test_verifier_fails_when_new_object_missing_or_identical(self):
        cs_missing = ChangeSet(
            operation="duplicate_object",
            target_name="Chair_02",
            expected_after={"source_name": "Chair", "source_exists": True, "new_exists": True},
            actual_after={
                "source_name": "Chair",
                "new_name": "Chair_02",
                "source_exists": True,
                "new_exists": False,
                "exists": False,
            },
        )
        res_missing = self.verifier.verify(cs_missing)
        self.assertEqual(res_missing.status, VerificationStatus.FAIL)

        cs_identical = ChangeSet(
            operation="duplicate_object",
            target_name="Chair",
            expected_after={"source_name": "Chair", "source_exists": True, "new_exists": True},
            actual_after={
                "source_name": "Chair",
                "new_name": "Chair",
                "source_exists": True,
                "new_exists": True,
                "distinct_identity": False,
            },
        )
        res_identical = self.verifier.verify(cs_identical)
        self.assertEqual(res_identical.status, VerificationStatus.FAIL)
        props = [m["property"] for m in res_identical.mismatches]
        self.assertIn("distinct_identity", props)

    def test_verifier_fails_on_transform_mismatch(self):
        cs = ChangeSet(
            operation="duplicate_object",
            target_name="Chair_02",
            expected_after={"location": [5.0, 0.0, 0.0]},
            actual_after={
                "source_name": "Chair",
                "new_name": "Chair_02",
                "source_exists": True,
                "new_exists": True,
                "distinct_identity": True,
                "location": [0.0, 0.0, 0.0],
            },
        )
        result = self.verifier.verify(cs)
        self.assertEqual(result.status, VerificationStatus.FAIL)
        props = [m["property"] for m in result.mismatches]
        self.assertIn("location", props)

    def test_build_change_set_from_result(self):
        args = {"source_name": "Table", "new_name": "Table_Copy", "location": [2.0, 4.0, 0.0]}
        result_data = {
            "duplicated": True,
            "source_name": "Table",
            "new_name": "Table_Copy",
            "source_exists": True,
            "new_exists": True,
            "type": "MESH",
            "location": [2.0, 4.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "distinct_identity": True,
            "before": {"location": [0.0, 0.0, 0.0]},
        }
        cs = build_change_set_from_result("duplicate_object", args, result_data)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.operation, "duplicate_object")
        self.assertEqual(cs.target_name, "Table_Copy")
        self.assertEqual(cs.expected_after["location"], [2.0, 4.0, 0.0])
        self.assertEqual(cs.expected_after["new_name"], "Table_Copy")
        self.assertTrue(cs.expected_after["distinct_identity"])


class TestDuplicateObjectPolicy(unittest.TestCase):
    """Test policy decisions and human-readable descriptions for duplicate_object."""

    def setUp(self):
        self.policy = ApprovalPolicy()
        self.tool = DuplicateObjectTool()

    def test_auto_approves_low_risk(self):
        call = ToolCall(
            call_id="c1",
            tool_name="duplicate_object",
            arguments={"source_name": "Cube", "new_name": "Cube_02"},
        )
        decision = self.policy.evaluate(self.tool, call)
        self.assertEqual(decision, ApprovalDecision.AUTO_APPROVE)

    def test_human_description_formats(self):
        desc1 = self.policy.create_human_description(
            "duplicate_object", {"source_name": "Chair", "new_name": "Chair_02"}
        )
        self.assertEqual(desc1, 'Duplicate "Chair" as "Chair_02"')

        desc2 = self.policy.create_human_description("duplicate_object", {"source_name": "Chair"})
        self.assertEqual(desc2, 'Duplicate "Chair"')


if __name__ == "__main__":
    unittest.main()
