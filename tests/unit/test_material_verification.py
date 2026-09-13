"""Unit tests for M6 Task 2 Material Mutation Verification.

Runs in Pure Python without requiring Blender (bpy).
"""

import unittest
from core.change_set import ChangeSet, VerificationStatus
from agent.verifier import ChangeVerifier, build_change_set_from_result


class TestMaterialVerificationSetMaterial(unittest.TestCase):
    """Test ChangeVerifier rules for set_material operations."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_set_material_exact_match_pass(self):
        cs = ChangeSet(
            operation="set_material",
            target_name="GlossyRed",
            expected_after={
                "base_color": [1.0, 0.0, 0.0, 1.0],
                "metallic": 0.8,
                "roughness": 0.2,
                "emission_color": [0.0, 1.0, 0.0, 1.0],
                "emission_strength": 3.0,
                "alpha": 0.95,
            },
            actual_after={
                "principled_bsdf": {
                    "base_color": [1.0, 0.0, 0.0, 1.0],
                    "metallic": 0.8,
                    "roughness": 0.2,
                    "emission_color": [0.0, 1.0, 0.0, 1.0],
                    "emission_strength": 3.0,
                    "alpha": 0.95,
                }
            },
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertEqual(len(res.mismatches), 0)
        self.assertIn("PASSED", res.summary)

    def test_set_material_within_epsilon_pass(self):
        cs = ChangeSet(
            operation="set_material",
            target_name="Gold",
            expected_after={
                "metallic": 0.9,
                "roughness": 0.1,
                "base_color": [1.0, 0.8, 0.2, 1.0],
            },
            actual_after={
                "principled_bsdf": {
                    "metallic": 0.9004,  # Within 1e-3
                    "roughness": 0.0996,  # Within 1e-3
                    "base_color": [1.0003, 0.7997, 0.2001, 1.0],  # Within 1e-3
                }
            },
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)
        self.assertEqual(res.status, VerificationStatus.PASS)

    def test_set_material_scalar_exceeds_epsilon_fail(self):
        cs = ChangeSet(
            operation="set_material",
            target_name="Mat",
            expected_after={
                "roughness": 0.2,
            },
            actual_after={
                "principled_bsdf": {
                    "roughness": 0.25,  # Exceeds 1e-3
                }
            },
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(len(res.mismatches), 1)
        self.assertEqual(res.mismatches[0]["property"], "roughness")
        self.assertEqual(res.mismatches[0]["expected"], 0.2)
        self.assertEqual(res.mismatches[0]["actual"], 0.25)
        self.assertAlmostEqual(res.mismatches[0]["diff"], 0.05, places=5)

    def test_set_material_color_vector_exceeds_epsilon_fail(self):
        cs = ChangeSet(
            operation="set_material",
            target_name="Mat",
            expected_after={
                "base_color": [1.0, 0.0, 0.0, 1.0],
            },
            actual_after={
                "principled_bsdf": {
                    "base_color": [0.0, 1.0, 0.0, 1.0],  # Green instead of Red
                }
            },
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(len(res.mismatches), 1)
        self.assertEqual(res.mismatches[0]["property"], "base_color")
        self.assertIn("mismatch in [base_color]", res.summary)

    def test_set_material_partial_fields_ignored_pass(self):
        # Only roughness was mutated; base_color, metallic, etc. on the material are ignored
        cs = ChangeSet(
            operation="set_material",
            target_name="Mat",
            expected_after={
                "roughness": 0.45,
            },
            actual_after={
                "principled_bsdf": {
                    "base_color": [0.1, 0.2, 0.3, 1.0],
                    "metallic": 0.77,
                    "roughness": 0.45,
                    "emission_strength": 12.0,
                }
            },
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)
        self.assertEqual(res.status, VerificationStatus.PASS)

    def test_set_material_missing_property_in_actual_fail(self):
        cs = ChangeSet(
            operation="set_material",
            target_name="Mat",
            expected_after={
                "metallic": 0.8,
            },
            actual_after={
                "principled_bsdf": {
                    "roughness": 0.5,
                }
            },
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.mismatches[0]["property"], "metallic")
        self.assertIsNone(res.mismatches[0]["actual"])

    def test_set_material_flat_actual_structure_pass(self):
        # Verify verifier supports flat actual_after (not nested in principled_bsdf)
        cs = ChangeSet(
            operation="set_material",
            target_name="Mat",
            expected_after={
                "metallic": 0.5,
                "roughness": 0.5,
            },
            actual_after={
                "metallic": 0.5,
                "roughness": 0.5,
            },
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)


class TestMaterialVerificationAssignMaterial(unittest.TestCase):
    """Test ChangeVerifier rules for assign_material operations."""

    def setUp(self):
        self.verifier = ChangeVerifier(epsilon=1e-3)

    def test_assign_material_success_pass(self):
        cs = ChangeSet(
            operation="assign_material",
            target_name="Cube",
            expected_after={
                "object_name": "Cube",
                "material_name": "RedGloss",
                "slot_index": 0,
            },
            actual_after={
                "object_name": "Cube",
                "material_name": "RedGloss",
                "slot_index": 0,
            },
        )
        res = self.verifier.verify(cs)
        self.assertTrue(res.passed)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertIn("PASSED", res.summary)

    def test_assign_material_wrong_material_name_fail(self):
        cs = ChangeSet(
            operation="assign_material",
            target_name="Cube",
            expected_after={
                "object_name": "Cube",
                "material_name": "RedGloss",
                "slot_index": 0,
            },
            actual_after={
                "object_name": "Cube",
                "material_name": "BlueGloss",
                "slot_index": 0,
            },
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(res.mismatches[0]["property"], "material_name")
        self.assertEqual(res.mismatches[0]["expected"], "RedGloss")
        self.assertEqual(res.mismatches[0]["actual"], "BlueGloss")

    def test_assign_material_wrong_slot_index_fail(self):
        cs = ChangeSet(
            operation="assign_material",
            target_name="Cube",
            expected_after={
                "object_name": "Cube",
                "material_name": "RedGloss",
                "slot_index": 0,
            },
            actual_after={
                "object_name": "Cube",
                "material_name": "RedGloss",
                "slot_index": 1,
            },
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.mismatches[0]["property"], "slot_index")
        self.assertEqual(res.mismatches[0]["expected"], 0)
        self.assertEqual(res.mismatches[0]["actual"], 1)
        self.assertEqual(res.mismatches[0]["diff"], 1)

    def test_assign_material_wrong_object_name_fail(self):
        cs = ChangeSet(
            operation="assign_material",
            target_name="Cube",
            expected_after={
                "object_name": "Cube",
                "material_name": "RedGloss",
                "slot_index": 0,
            },
            actual_after={
                "object_name": "Sphere",
                "material_name": "RedGloss",
                "slot_index": 0,
            },
        )
        res = self.verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.mismatches[0]["property"], "object_name")


class TestMaterialChangeSetBuilder(unittest.TestCase):
    """Test build_change_set_from_result for material tools."""

    def test_build_change_set_set_material(self):
        arguments = {
            "object_name": "Cube",
            "base_color": [1.0, 0.5, 0.0],  # 3-element RGB
            "metallic": 0.8,
            "roughness": 0.2,
        }
        result_data = {
            "material_name": "Cube_Material",
            "object_name": "Cube",
            "actual": {
                "material_name": "Cube_Material",
                "principled_bsdf": {
                    "base_color": [1.0, 0.5, 0.0, 1.0],
                    "metallic": 0.8,
                    "roughness": 0.2,
                },
            },
        }
        cs = build_change_set_from_result("set_material", arguments, result_data)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.operation, "set_material")
        self.assertEqual(cs.target_name, "Cube_Material")
        self.assertEqual(cs.expected_after["base_color"], [1.0, 0.5, 0.0, 1.0])
        self.assertEqual(cs.expected_after["metallic"], 0.8)
        self.assertEqual(cs.expected_after["roughness"], 0.2)
        self.assertIn("principled_bsdf", cs.actual_after)

    def test_build_change_set_assign_material(self):
        arguments = {
            "object_name": "TargetObj",
            "material_name": "TargetMat",
            "slot_index": 2,
        }
        result_data = {
            "assigned": True,
            "object_name": "TargetObj",
            "material_name": "TargetMat",
            "slot_index": 2,
            "actual": {
                "object_name": "TargetObj",
                "material_name": "TargetMat",
                "slot_index": 2,
            },
        }
        cs = build_change_set_from_result("assign_material", arguments, result_data)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.operation, "assign_material")
        self.assertEqual(cs.target_name, "TargetObj")
        self.assertEqual(cs.expected_after["object_name"], "TargetObj")
        self.assertEqual(cs.expected_after["material_name"], "TargetMat")
        self.assertEqual(cs.expected_after["slot_index"], 2)


class TestUnsupportedOperationBehavior(unittest.TestCase):
    """Verify unsupported operation handling is preserved."""

    def test_unsupported_operation_returns_fail(self):
        verifier = ChangeVerifier()
        cs = ChangeSet(
            operation="unsupported_shader_op",
            target_name="Cube",
        )
        res = verifier.verify(cs)
        self.assertFalse(res.passed)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(res.mismatches[0]["property"], "operation")


if __name__ == "__main__":
    unittest.main()
