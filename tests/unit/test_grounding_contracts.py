"""Unit tests for Phase 4 grounding tool contracts and schemas.

Runs in pure Python without Blender.
"""

import unittest
from core.types import RiskLevel, ToolResult
from tools.read_only.inspect_scene import InspectSceneTool
from tools.read_only.inspect_selection import InspectSelectionTool
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_material import InspectMaterialTool
from tools.read_only.inspect_mesh import InspectMeshTool
from tools.registry import ToolRegistry


class MockAdapter:
    """Mock adapter simulating BlenderAdapter responses."""

    def inspect_scene(self) -> ToolResult:
        return ToolResult.ok("inspect_scene", {"scene_name": "MockScene"})

    def inspect_selection(self) -> ToolResult:
        return ToolResult.ok("inspect_selection", {"active_object": "MockCube"})

    def inspect_object(self, name: str, include_evaluated: bool = False) -> ToolResult:
        if name == "Ghost":
            return ToolResult.fail("inspect_object", "OBJECT_NOT_FOUND", "Object not found")
        return ToolResult.ok("inspect_object", {"name": name, "type": "MESH"})

    def inspect_material(self, material_name=None, object_name=None, slot_index=0) -> ToolResult:
        if material_name == "GhostMat":
            return ToolResult.fail("inspect_material", "MATERIAL_NOT_FOUND", "Material not found")
        if object_name == "GhostObj":
            return ToolResult.fail("inspect_material", "OBJECT_NOT_FOUND", "Object not found")
        return ToolResult.ok("inspect_material", {
            "material_name": material_name or "SlotMat",
            "use_nodes": True,
        })

    def inspect_mesh(self, object_name: str) -> ToolResult:
        if object_name == "Camera":
            return ToolResult.fail("inspect_mesh", "INVALID_DATA_TYPE", "Camera is not a MESH")
        if object_name == "Ghost":
            return ToolResult.fail("inspect_mesh", "OBJECT_NOT_FOUND", "Object not found")
        return ToolResult.ok("inspect_mesh", {
            "object_name": object_name,
            "counts": {"vertices": 8, "edges": 12, "polygons": 6},
        })


class TestGroundingToolsContract(unittest.TestCase):
    """Verify tool contract adherence and execution dispatch."""

    def setUp(self):
        self.adapter = MockAdapter()
        self.scene_tool = InspectSceneTool()
        self.selection_tool = InspectSelectionTool()
        self.object_tool = InspectObjectTool()
        self.material_tool = InspectMaterialTool()
        self.mesh_tool = InspectMeshTool()

    def test_tool_contract_properties(self):
        tools = [self.scene_tool, self.selection_tool, self.object_tool, self.material_tool, self.mesh_tool]
        for tool in tools:
            self.assertEqual(tool.risk_level, RiskLevel.READ_ONLY)
            self.assertIsInstance(tool.name, str)
            self.assertIsInstance(tool.description, str)
            self.assertIsInstance(tool.input_schema, dict)

    def test_mock_tool_execution(self):
        res_scene = self.scene_tool.execute(self.adapter)
        self.assertTrue(res_scene.success)

        res_sel = self.selection_tool.execute(self.adapter)
        self.assertTrue(res_sel.success)

        res_obj = self.object_tool.execute(self.adapter, name="Cube")
        self.assertTrue(res_obj.success)

    def test_material_tool_dual_entry_validation(self):
        # 1. Neither provided -> fail
        res_neither = self.material_tool.execute(self.adapter)
        self.assertFalse(res_neither.success)
        self.assertEqual(res_neither.error.type, "INVALID_ARGUMENT")

        # 2. Both provided -> fail
        res_both = self.material_tool.execute(self.adapter, material_name="Mat", object_name="Cube")
        self.assertFalse(res_both.success)
        self.assertEqual(res_both.error.type, "INVALID_ARGUMENT")

        # 3. Valid material_name -> success
        res_mat = self.material_tool.execute(self.adapter, material_name="Material")
        self.assertTrue(res_mat.success)
        self.assertEqual(res_mat.data["material_name"], "Material")

        # 4. Valid object_name + slot -> success
        res_slot = self.material_tool.execute(self.adapter, object_name="Cube", slot_index=0)
        self.assertTrue(res_slot.success)
        self.assertEqual(res_slot.data["material_name"], "SlotMat")

    def test_mesh_tool_execution(self):
        # 1. Missing / invalid object_name -> fail
        res_empty = self.mesh_tool.execute(self.adapter, object_name="")
        self.assertFalse(res_empty.success)
        self.assertEqual(res_empty.error.type, "INVALID_ARGUMENT")

        # 2. Valid mesh object -> success
        res_cube = self.mesh_tool.execute(self.adapter, object_name="Cube")
        self.assertTrue(res_cube.success)
        self.assertEqual(res_cube.data["counts"]["vertices"], 8)

        # 3. Non-mesh object -> fail with INVALID_DATA_TYPE
        res_cam = self.mesh_tool.execute(self.adapter, object_name="Camera")
        self.assertFalse(res_cam.success)
        self.assertEqual(res_cam.error.type, "INVALID_DATA_TYPE")

    def test_all_five_tools_in_registry(self):
        registry = ToolRegistry()
        tools = [self.scene_tool, self.selection_tool, self.object_tool, self.material_tool, self.mesh_tool]
        for t in tools:
            registry.register(t)

        self.assertEqual(len(registry), 5)
        schemas = registry.export_schemas()
        names = [s["name"] for s in schemas]
        expected = ["inspect_material", "inspect_mesh", "inspect_object", "inspect_scene", "inspect_selection"]
        self.assertEqual(names, expected)


if __name__ == "__main__":
    unittest.main()
