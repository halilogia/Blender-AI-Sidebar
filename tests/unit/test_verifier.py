"""Unit tests for mutation ChangeSets and ChangeVerifier.

Zero Blender (bpy) dependencies. Pure Python test suite.
"""

import math
import unittest

from core.change_set import ChangeSet, VerificationResult, VerificationStatus
from agent.verifier import ChangeVerifier, normalize_angle, angle_difference


class TestChangeVerifierModels(unittest.TestCase):
    """Test immutability, serialization, and properties of ChangeSet and VerificationResult."""

    def test_change_set_immutability(self):
        cs = ChangeSet(
            operation="create",
            target_name="Cube",
            expected_after={"location": [0.0, 0.0, 0.0]},
            actual_after={"location": [0.0, 0.0, 0.0]},
        )
        with self.assertRaises(Exception):
            cs.target_name = "Sphere"  # type: ignore

    def test_change_set_to_dict(self):
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            before={"location": [0.0, 0.0, 0.0]},
            expected_after={"location": [1.0, 0.0, 0.0]},
            actual_after={"location": [1.0, 0.0, 0.0]},
        )
        d = cs.to_dict()
        self.assertEqual(d["operation"], "transform")
        self.assertEqual(d["target_name"], "Cube")
        self.assertEqual(d["before"], {"location": [0.0, 0.0, 0.0]})
        self.assertEqual(d["expected_after"], {"location": [1.0, 0.0, 0.0]})
        self.assertEqual(d["actual_after"], {"location": [1.0, 0.0, 0.0]})

    def test_verification_result_passed_property_and_to_dict(self):
        vr_pass = VerificationResult(
            status=VerificationStatus.PASS,
            operation="create",
            target_name="Cube",
            mismatches=[],
            summary="All good",
        )
        self.assertTrue(vr_pass.passed)
        self.assertEqual(vr_pass.to_dict()["status"], "PASS")
        self.assertTrue(vr_pass.to_dict()["passed"])

        vr_fail = VerificationResult(
            status=VerificationStatus.FAIL,
            operation="create",
            target_name="Cube",
            mismatches=[{"property": "exists", "expected": True, "actual": False, "diff": None}],
            summary="Failed",
        )
        self.assertFalse(vr_fail.passed)
        self.assertEqual(vr_fail.to_dict()["status"], "FAIL")
        self.assertFalse(vr_fail.to_dict()["passed"])


class TestChangeVerifierCreate(unittest.TestCase):
    """Test verification rules for the 'create' operation."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_create_pass(self):
        cs = ChangeSet(
            operation="create",
            target_name="Cube",
            expected_after={
                "primitive_type": "CUBE",
                "location": [2.0, 0.0, 1.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "vertex_count": 8,
            },
            actual_after={
                "exists": True,
                "primitive_type": "CUBE",
                "location": [2.0, 0.0, 1.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "vertex_count": 8,
            },
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.mismatches), 0)
        self.assertIn("PASSED", res.summary)

    def test_create_object_missing_fail(self):
        cs = ChangeSet(
            operation="create",
            target_name="Cube",
            expected_after={"location": [0.0, 0.0, 0.0]},
            actual_after={"exists": False},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertFalse(res.passed)
        self.assertEqual(len(res.mismatches), 1)
        self.assertEqual(res.mismatches[0]["property"], "exists")
        self.assertEqual(res.mismatches[0]["expected"], True)
        self.assertEqual(res.mismatches[0]["actual"], False)

    def test_create_wrong_type_fail(self):
        cs = ChangeSet(
            operation="create",
            target_name="Cube",
            expected_after={"primitive_type": "CUBE"},
            actual_after={"exists": True, "primitive_type": "SPHERE"},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(len(res.mismatches), 1)
        self.assertEqual(res.mismatches[0]["property"], "type")
        self.assertEqual(res.mismatches[0]["expected"], "CUBE")
        self.assertEqual(res.mismatches[0]["actual"], "SPHERE")

    def test_create_zero_geometry_fail(self):
        cs = ChangeSet(
            operation="create",
            target_name="Cube",
            expected_after={},
            actual_after={"exists": True, "vertex_count": 0},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(res.mismatches[0]["property"], "vertex_count")

    def test_create_missing_field_fail(self):
        cs = ChangeSet(
            operation="create",
            target_name="Cube",
            expected_after={"location": [1.0, 2.0, 3.0]},
            actual_after={"exists": True},  # Missing location
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(res.mismatches[0]["property"], "location")
        self.assertIsNone(res.mismatches[0]["actual"])


class TestChangeVerifierToleranceAndNumerics(unittest.TestCase):
    """Test floating-point epsilon tolerance and diff calculation."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_float_epsilon_pass(self):
        # Difference is 0.0004 < 0.001 (EPSILON)
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            expected_after={"location": [1.0, 2.0, 3.0]},
            actual_after={"exists": True, "location": [1.0004, 1.9997, 3.0002]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertEqual(len(res.mismatches), 0)

    def test_epsilon_exceeded_fail_with_diff_accuracy(self):
        # Difference is 0.002 > 0.001 on X
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            expected_after={"location": [1.0, 2.0, 3.0]},
            actual_after={"exists": True, "location": [1.002, 2.0, 3.0]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(len(res.mismatches), 1)
        m = res.mismatches[0]
        self.assertEqual(m["property"], "location")
        self.assertEqual(m["expected"], [1.0, 2.0, 3.0])
        self.assertEqual(m["actual"], [1.002, 2.0, 3.0])
        self.assertAlmostEqual(m["diff"][0], 0.002, places=5)
        self.assertAlmostEqual(m["diff"][1], 0.0, places=5)
        self.assertAlmostEqual(m["diff"][2], 0.0, places=5)

    def test_rotation_euler_wrapping_pass(self):
        # In radians, pi (3.14159265) and -pi (-3.14159265) represent the same angular orientation
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            expected_after={"rotation": [0.0, math.pi, 0.0]},
            actual_after={"exists": True, "rotation": [0.0, -math.pi, 0.0]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertEqual(len(res.mismatches), 0)

    def test_rotation_mismatch_fail(self):
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            expected_after={"rotation": [0.0, 0.0, 0.0]},
            actual_after={"exists": True, "rotation": [0.0, math.pi / 2.0, 0.0]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(len(res.mismatches), 1)
        self.assertEqual(res.mismatches[0]["property"], "rotation")
        self.assertAlmostEqual(res.mismatches[0]["diff"][1], round(math.pi / 2.0, 6), places=4)


class TestChangeVerifierTransform(unittest.TestCase):
    """Test verification rules for the 'transform' operation."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_transform_pass_selective_fields(self):
        # Only location was modified; rotation and scale are not in expected_after
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            before={"location": [0.0, 0.0, 0.0]},
            expected_after={"location": [5.0, -2.5, 10.0]},
            actual_after={"exists": True, "location": [5.0, -2.5, 10.0], "rotation": [0.0, 0.0, 0.0]},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)

    def test_transform_object_missing_fail(self):
        cs = ChangeSet(
            operation="transform",
            target_name="MissingObj",
            expected_after={"location": [1.0, 0.0, 0.0]},
            actual_after={"exists": False},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(res.mismatches[0]["property"], "exists")


class TestChangeVerifierDelete(unittest.TestCase):
    """Test verification rules for the 'delete' operation."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_delete_pass_when_exists_false(self):
        cs = ChangeSet(
            operation="delete",
            target_name="Cube",
            before={"location": [0.0, 0.0, 0.0]},
            expected_after={"exists": False},
            actual_after={"exists": False, "deleted": True},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.mismatches), 0)

    def test_delete_fail_when_object_still_exists(self):
        cs = ChangeSet(
            operation="delete",
            target_name="Cube",
            before={"location": [0.0, 0.0, 0.0]},
            expected_after={"exists": False},
            actual_after={"exists": True, "deleted": False},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertFalse(res.passed)
        self.assertEqual(len(res.mismatches), 1)
        self.assertEqual(res.mismatches[0]["property"], "exists")
        self.assertEqual(res.mismatches[0]["expected"], False)
        self.assertEqual(res.mismatches[0]["actual"], True)


class TestChangeVerifierEdgeCases(unittest.TestCase):
    """Test unhandled operations and malformed vectors."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_unsupported_operation_fail(self):
        cs = ChangeSet(
            operation="extrude",
            target_name="Cube",
            expected_after={},
            actual_after={},
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertIn("Unsupported operation", res.summary)

    def test_vector_dimension_mismatch_fail(self):
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            expected_after={"location": [1.0, 2.0, 3.0]},
            actual_after={"exists": True, "location": [1.0, 2.0]},  # Only 2 items
        )
        res = self.verifier.verify(cs)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(res.mismatches[0]["property"], "location")


class TestAdapterSnapshotContracts(unittest.TestCase):
    """Test that standardized adapter mutation snapshots seamlessly pass verification."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_create_primitive_snapshot_contract(self):
        # Exact output format of PrimitiveMutator.create
        create_snapshot = {
            "created": True,
            "exists": True,
            "object_name": "Cube",
            "primitive_type": "CUBE",
            "type": "MESH",
            "location": [1.0, 2.0, 3.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "vertex_count": 8,
            "face_count": 6,
        }
        cs = ChangeSet(
            operation="create",
            target_name="Cube",
            expected_after={
                "primitive_type": "CUBE",
                "location": [1.0, 2.0, 3.0],
                "vertex_count": 8,
            },
            actual_after=create_snapshot,
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.mismatches), 0)

    def test_transform_object_snapshot_contract(self):
        # Exact output format of TransformMutator.transform
        transform_snapshot = {
            "object_name": "Cube",
            "relative": False,
            "exists": True,
            "before": {"location": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
            "after": {"exists": True, "location": [2.0, 3.0, 4.0], "rotation": [0.0, 0.0, 1.5708], "scale": [1.5, 1.5, 2.0]},
            "actual": {"exists": True, "location": [2.0, 3.0, 4.0], "rotation": [0.0, 0.0, 1.5708], "scale": [1.5, 1.5, 2.0]},
            "changed": ["location", "rotation", "scale"],
        }
        cs = ChangeSet(
            operation="transform",
            target_name="Cube",
            before=transform_snapshot["before"],
            expected_after={"location": [2.0, 3.0, 4.0], "scale": [1.5, 1.5, 2.0]},
            actual_after=transform_snapshot["actual"],
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.mismatches), 0)

    def test_delete_object_snapshot_contract(self):
        # Exact output format of DeleteMutator.delete
        delete_snapshot = {
            "deleted": True,
            "exists": False,
            "object_name": "Cube",
            "type": "MESH",
            "previous_state": {"location": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
        }
        cs = ChangeSet(
            operation="delete",
            target_name="Cube",
            before=delete_snapshot["previous_state"],
            expected_after={"exists": False},
            actual_after=delete_snapshot,
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)
        self.assertEqual(len(res.mismatches), 0)


if __name__ == "__main__":
    unittest.main()
