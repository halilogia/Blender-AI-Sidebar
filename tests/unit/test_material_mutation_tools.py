"""Unit tests for M6 Task 1 Material Mutation Tools (set_material, assign_material).

Runs in Pure Python without requiring Blender.
"""

import sys
import unittest
from unittest.mock import MagicMock

# Ensure mock bpy, bmesh, and mathutils are available if running outside live Blender
if "bpy" not in sys.modules:
    sys.modules["bpy"] = MagicMock()
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = MagicMock()
if "mathutils" not in sys.modules:
    sys.modules["mathutils"] = MagicMock()

from core.types import RiskLevel, ToolResult
from tools.registry import ToolRegistry
from agent.dispatcher import ToolDispatcher
from agent.models import ToolCall
from tools.mutations.set_material import SetMaterialTool
from tools.mutations.assign_material import AssignMaterialTool
from adapter.mutators.material_mutator import (
    clamp_float,
    normalize_color,
    normalize_emission_strength,
    find_input_socket,
    MaterialMutator,
)


class TestMaterialMutationToolContracts(unittest.TestCase):
    """Verify tool contract definitions, schemas, and risk classifications."""

    def test_set_material_contract(self):
        tool = SetMaterialTool()
        self.assertEqual(tool.name, "set_material")
        self.assertEqual(tool.risk_level, RiskLevel.LOW)
        self.assertFalse(tool.input_schema["additionalProperties"])
        props = tool.input_schema["properties"]
        self.assertIn("object_name", props)
        self.assertIn("material_name", props)
        self.assertIn("slot_index", props)
        self.assertIn("base_color", props)
        self.assertIn("metallic", props)
        self.assertIn("roughness", props)
        self.assertIn("emission_color", props)
        self.assertIn("emission_strength", props)
        self.assertIn("alpha", props)

        schema = tool.to_schema()
        self.assertEqual(schema["name"], "set_material")
        self.assertEqual(schema["risk_level"], "LOW")

    def test_assign_material_contract(self):
        tool = AssignMaterialTool()
        self.assertEqual(tool.name, "assign_material")
        self.assertEqual(tool.risk_level, RiskLevel.LOW)
        self.assertEqual(tool.input_schema["required"], ["object_name", "material_name"])
        self.assertFalse(tool.input_schema["additionalProperties"])
        props = tool.input_schema["properties"]
        self.assertIn("object_name", props)
        self.assertIn("material_name", props)
        self.assertIn("slot_index", props)

        schema = tool.to_schema()
        self.assertEqual(schema["name"], "assign_material")
        self.assertEqual(schema["risk_level"], "LOW")


class TestMaterialDispatcherExecution(unittest.TestCase):
    """Verify ToolDispatcher validation, argument checks, and mock adapter delegation."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(SetMaterialTool())
        self.registry.register(AssignMaterialTool())

        self.mock_adapter = MagicMock()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.mock_adapter)

    def test_set_material_dispatch_by_object_name(self):
        self.mock_adapter.set_material.return_value = ToolResult.ok(
            "set_material",
            {"material_name": "Cube_Material", "changed": ["base_color"]},
        )

        tc = ToolCall(
            call_id="call_mat_1",
            tool_name="set_material",
            arguments={"object_name": "Cube", "base_color": [1.0, 0.0, 0.0]},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertTrue(res.success)
        self.assertEqual(res.tool, "set_material")
        self.assertEqual(res.data["material_name"], "Cube_Material")
        self.mock_adapter.set_material.assert_called_once_with(
            object_name="Cube",
            material_name=None,
            slot_index=0,
            base_color=[1.0, 0.0, 0.0],
            metallic=None,
            roughness=None,
            emission_color=None,
            emission_strength=None,
            alpha=None,
        )

    def test_set_material_dispatch_by_material_name_all_fields(self):
        self.mock_adapter.set_material.return_value = ToolResult.ok(
            "set_material",
            {"material_name": "Gold", "changed": ["metallic", "roughness"]},
        )

        tc = ToolCall(
            call_id="call_mat_2",
            tool_name="set_material",
            arguments={
                "material_name": "Gold",
                "base_color": [1.0, 0.8, 0.2, 1.0],
                "metallic": 1.0,
                "roughness": 0.1,
                "emission_color": [0.0, 0.0, 0.0],
                "emission_strength": 0.0,
                "alpha": 1.0,
            },
        )
        res = self.dispatcher.dispatch(tc)

        self.assertTrue(res.success)
        self.mock_adapter.set_material.assert_called_once_with(
            object_name=None,
            material_name="Gold",
            slot_index=0,
            base_color=[1.0, 0.8, 0.2, 1.0],
            metallic=1.0,
            roughness=0.1,
            emission_color=[0.0, 0.0, 0.0],
            emission_strength=0.0,
            alpha=1.0,
        )

    def test_set_material_unexpected_argument_rejected(self):
        tc = ToolCall(
            call_id="call_mat_3",
            tool_name="set_material",
            arguments={"object_name": "Cube", "specular": 0.5},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("specular", res.error.message)
        self.mock_adapter.set_material.assert_not_called()

    def test_assign_material_dispatch_success(self):
        self.mock_adapter.assign_material.return_value = ToolResult.ok(
            "assign_material",
            {"assigned": True, "object_name": "Cube", "material_name": "Gold"},
        )

        tc = ToolCall(
            call_id="call_mat_4",
            tool_name="assign_material",
            arguments={"object_name": "Cube", "material_name": "Gold", "slot_index": 1},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertTrue(res.success)
        self.assertEqual(res.tool, "assign_material")
        self.mock_adapter.assign_material.assert_called_once_with(
            object_name="Cube",
            material_name="Gold",
            slot_index=1,
        )

    def test_assign_material_missing_required_arguments(self):
        tc = ToolCall(
            call_id="call_mat_5",
            tool_name="assign_material",
            arguments={"object_name": "Cube"},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("material_name", res.error.message)
        self.mock_adapter.assign_material.assert_not_called()


class TestMaterialHelperFunctions(unittest.TestCase):
    """Verify color normalization, clamping, and socket finder logic."""

    def test_clamp_float(self):
        self.assertEqual(clamp_float(0.5), 0.5)
        self.assertEqual(clamp_float(-0.2), 0.0)
        self.assertEqual(clamp_float(1.5), 1.0)
        self.assertEqual(clamp_float("0.75"), 0.75)
        # NaN / Inf fallback
        self.assertEqual(clamp_float(float("nan")), 0.0)
        self.assertEqual(clamp_float(float("inf")), 0.0)
        with self.assertRaises(ValueError):
            clamp_float("invalid_str")

    def test_normalize_color_rgb(self):
        # 3 items (RGB) -> alpha defaults to 1.0
        norm = normalize_color([0.2, 0.4, 0.6])
        self.assertEqual(norm, [0.2, 0.4, 0.6, 1.0])

    def test_normalize_color_rgba(self):
        # 4 items (RGBA) -> alpha preserved
        norm = normalize_color([1.2, -0.1, 0.5, 0.8])
        self.assertEqual(norm, [1.0, 0.0, 0.5, 0.8])

    def test_normalize_color_invalid_shapes(self):
        with self.assertRaises(ValueError):
            normalize_color([1.0, 0.0])  # 2 elements
        with self.assertRaises(ValueError):
            normalize_color([1.0, 0.0, 0.0, 1.0, 0.5])  # 5 elements
        with self.assertRaises(ValueError):
            normalize_color("red")  # Not a list/tuple

    def test_normalize_emission_strength(self):
        self.assertEqual(normalize_emission_strength(5.0), 5.0)
        self.assertEqual(normalize_emission_strength(-2.0), 0.0)
        self.assertEqual(normalize_emission_strength(0.0), 0.0)
        self.assertEqual(normalize_emission_strength(float("nan")), 0.0)
        with self.assertRaises(ValueError):
            normalize_emission_strength("invalid")

    def test_find_input_socket(self):
        sock1 = MagicMock()
        sock1.name = "Base Color"
        sock1.identifier = "Base Color"

        sock2 = MagicMock()
        sock2.name = "Emission Color"
        sock2.identifier = "Emission Color"

        class MockInputs:
            def __init__(self, sockets):
                self._sockets = sockets

            def __iter__(self):
                return iter(self._sockets)

            def get(self, name):
                for s in self._sockets:
                    if getattr(s, "name", None) == name:
                        return s
                return None

        node = MagicMock()
        node.inputs = MockInputs([sock1, sock2])

        # Exact match
        self.assertEqual(find_input_socket(node, "Base Color"), sock1)
        # Normalized match (case / space insensitive)
        self.assertEqual(find_input_socket(node, "base_color"), sock1)
        self.assertEqual(find_input_socket(node, "emissioncolor"), sock2)
        # Missing socket
        self.assertIsNone(find_input_socket(node, "Subsurface"))


class TestMaterialMutatorValidation(unittest.TestCase):
    """Verify input validation guards in MaterialMutator."""

    def test_set_material_requires_at_least_one_property(self):
        with self.assertRaises(ValueError) as ctx:
            MaterialMutator.set_material(object_name="Cube")
        self.assertIn("At least one material property", str(ctx.exception))

    def test_set_material_requires_target(self):
        with self.assertRaises(ValueError) as ctx:
            MaterialMutator.set_material(base_color=[1.0, 0.0, 0.0])
        self.assertIn("Must provide at least one of 'object_name' or 'material_name'", str(ctx.exception))

    def test_set_material_invalid_slot_index(self):
        with self.assertRaises(ValueError) as ctx:
            MaterialMutator.set_material(object_name="Cube", base_color=[1.0, 0.0, 0.0], slot_index=-1)
        self.assertIn("non-negative integer", str(ctx.exception))

    def test_assign_material_invalid_arguments(self):
        with self.assertRaises(ValueError):
            MaterialMutator.assign_material(object_name="", material_name="Mat")
        with self.assertRaises(ValueError):
            MaterialMutator.assign_material(object_name="Cube", material_name="")
        with self.assertRaises(ValueError):
            MaterialMutator.assign_material(object_name="Cube", material_name="Mat", slot_index=-1)


if __name__ == "__main__":
    unittest.main()
