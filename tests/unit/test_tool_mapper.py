"""Unit tests for OpenAICompatibleToolMapper (M2.3.1 - M2.3.2).

Zero Blender dependencies. Pure Python.
"""

import json
import unittest

from agent.tool_mapper import OpenAICompatibleToolMapper, ToolMappingError
from tools.read_only.inspect_scene import InspectSceneTool
from tools.read_only.inspect_selection import InspectSelectionTool
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_material import InspectMaterialTool
from tools.read_only.inspect_mesh import InspectMeshTool
from tools.registry import ToolRegistry


class TestOpenAICompatibleToolMapper(unittest.TestCase):
    """Tests for mapping grounding tools to OpenAI function schema."""

    def test_inspect_scene_mapping(self):
        tool = InspectSceneTool()
        mapped = OpenAICompatibleToolMapper.map_tool(tool)

        self.assertEqual(mapped["type"], "function")
        fn = mapped["function"]
        self.assertEqual(fn["name"], "inspect_scene")
        self.assertEqual(fn["description"], tool.description)
        self.assertEqual(fn["parameters"]["type"], "object")
        self.assertEqual(fn["parameters"]["properties"], {})
        self.assertFalse(fn["parameters"]["additionalProperties"])

    def test_inspect_selection_mapping(self):
        tool = InspectSelectionTool()
        mapped = OpenAICompatibleToolMapper.map_tool(tool)

        self.assertEqual(mapped["type"], "function")
        fn = mapped["function"]
        self.assertEqual(fn["name"], "inspect_selection")
        self.assertEqual(fn["description"], tool.description)
        self.assertEqual(fn["parameters"]["type"], "object")

    def test_inspect_object_mapping(self):
        tool = InspectObjectTool()
        mapped = OpenAICompatibleToolMapper.map_tool(tool)

        self.assertEqual(mapped["type"], "function")
        fn = mapped["function"]
        self.assertEqual(fn["name"], "inspect_object")
        self.assertIn("name", fn["parameters"]["properties"])
        self.assertEqual(fn["parameters"]["properties"]["name"]["type"], "string")
        self.assertEqual(fn["parameters"]["required"], ["name"])

    def test_inspect_material_mapping(self):
        tool = InspectMaterialTool()
        mapped = OpenAICompatibleToolMapper.map_tool(tool)

        fn = mapped["function"]
        self.assertEqual(fn["name"], "inspect_material")
        props = fn["parameters"]["properties"]
        self.assertIn("material_name", props)
        self.assertIn("object_name", props)
        self.assertIn("slot_index", props)

    def test_inspect_mesh_mapping(self):
        tool = InspectMeshTool()
        mapped = OpenAICompatibleToolMapper.map_tool(tool)

        fn = mapped["function"]
        self.assertEqual(fn["name"], "inspect_mesh")
        self.assertIn("object_name", fn["parameters"]["properties"])
        self.assertEqual(fn["parameters"]["required"], ["object_name"])

    def test_map_all_five_tools_preserves_registry_order(self):
        registry = ToolRegistry()
        registry.register(InspectSceneTool())
        registry.register(InspectSelectionTool())
        registry.register(InspectObjectTool())
        registry.register(InspectMaterialTool())
        registry.register(InspectMeshTool())

        tools = registry.list()
        # Alphabetical order: inspect_material, inspect_mesh, inspect_object, inspect_scene, inspect_selection
        expected_names = [t.name for t in tools]

        mapped_list = OpenAICompatibleToolMapper.map_tools(tools)
        actual_names = [m["function"]["name"] for m in mapped_list]
        self.assertEqual(actual_names, expected_names)

    def test_missing_or_empty_name_raises(self):
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "", "description": "desc", "input_schema": {}})
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"description": "desc", "input_schema": {}})
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "   ", "description": "desc", "input_schema": {}})

    def test_missing_or_empty_description_raises(self):
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "tool_a", "description": "", "input_schema": {}})
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "tool_a", "description": "   ", "input_schema": {}})
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "tool_a", "input_schema": {}})

    def test_missing_or_invalid_input_schema_raises(self):
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "tool_a", "description": "desc"})
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "tool_a", "description": "desc", "input_schema": "invalid"})
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({"name": "tool_a", "description": "desc", "input_schema": 123})

    def test_invalid_json_schema_shape_raises(self):
        # type not object
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({
                "name": "tool_a",
                "description": "desc",
                "input_schema": {"type": "array"}
            })
        # properties not dict
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({
                "name": "tool_a",
                "description": "desc",
                "input_schema": {"properties": "not_a_dict"}
            })
        # required not list
        with self.assertRaises(ToolMappingError):
            OpenAICompatibleToolMapper.map_tool({
                "name": "tool_a",
                "description": "desc",
                "input_schema": {"required": "not_a_list"}
            })

    def test_deterministic_output_and_json_serialization(self):
        tool = InspectObjectTool()
        mapped_1 = OpenAICompatibleToolMapper.map_tool(tool)
        mapped_2 = OpenAICompatibleToolMapper.map_tool(tool.to_schema())

        json_1 = json.dumps(mapped_1, sort_keys=True)
        json_2 = json.dumps(mapped_2, sort_keys=True)
        self.assertEqual(json_1, json_2)

        # Verify exact shape
        obj = json.loads(json_1)
        self.assertEqual(obj["type"], "function")
        self.assertIn("function", obj)
        self.assertIn("parameters", obj["function"])


if __name__ == "__main__":
    unittest.main()
