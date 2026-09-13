"""Unit tests for M3.1 Mutation Tools (create_primitive, transform_object, delete_object).

Zero Blender (bpy) dependencies. Pure Python.
"""

import unittest
from unittest.mock import MagicMock

from core.types import RiskLevel, ToolResult
from tools.base import InvalidToolContractError
from tools.registry import ToolRegistry
from agent.dispatcher import ToolDispatcher
from agent.models import ToolCall
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.transform_object import TransformObjectTool
from tools.mutations.delete_object import DeleteObjectTool


class TestMutationToolsContracts(unittest.TestCase):
    """Verify tool contract definitions, schemas, and risk classifications."""

    def test_create_primitive_contract(self):
        tool = CreatePrimitiveTool()
        self.assertEqual(tool.name, "create_primitive")
        self.assertEqual(tool.risk_level, RiskLevel.LOW)
        self.assertIn("CUBE", tool.input_schema["properties"]["primitive_type"]["enum"])
        self.assertIn("SPHERE", tool.input_schema["properties"]["primitive_type"]["enum"])
        self.assertIn("PLANE", tool.input_schema["properties"]["primitive_type"]["enum"])
        self.assertEqual(tool.input_schema["required"], ["primitive_type"])
        self.assertFalse(tool.input_schema["additionalProperties"])

        schema = tool.to_schema()
        self.assertEqual(schema["name"], "create_primitive")
        self.assertEqual(schema["risk_level"], "LOW")

    def test_transform_object_contract(self):
        tool = TransformObjectTool()
        self.assertEqual(tool.name, "transform_object")
        self.assertEqual(tool.risk_level, RiskLevel.LOW)
        self.assertEqual(tool.input_schema["required"], ["name"])
        self.assertFalse(tool.input_schema["additionalProperties"])
        self.assertIn("location", tool.input_schema["properties"])
        self.assertIn("rotation", tool.input_schema["properties"])
        self.assertIn("scale", tool.input_schema["properties"])
        self.assertIn("relative", tool.input_schema["properties"])

        schema = tool.to_schema()
        self.assertEqual(schema["name"], "transform_object")
        self.assertEqual(schema["risk_level"], "LOW")

    def test_delete_object_contract(self):
        tool = DeleteObjectTool()
        self.assertEqual(tool.name, "delete_object")
        self.assertEqual(tool.risk_level, RiskLevel.MEDIUM)
        self.assertEqual(tool.input_schema["required"], ["name"])
        self.assertFalse(tool.input_schema["additionalProperties"])

        schema = tool.to_schema()
        self.assertEqual(schema["name"], "delete_object")
        self.assertEqual(schema["risk_level"], "MEDIUM")


class TestMutationDispatcherExecution(unittest.TestCase):
    """Verify ToolDispatcher validation, argument checks, and mock adapter delegation."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(TransformObjectTool())
        self.registry.register(DeleteObjectTool())

        self.mock_adapter = MagicMock()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.mock_adapter)

    def test_create_primitive_dispatch_success(self):
        self.mock_adapter.create_primitive.return_value = ToolResult.ok(
            "create_primitive",
            {"created": True, "object_name": "Cube", "primitive_type": "CUBE"},
        )

        tc = ToolCall(
            call_id="call_1",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [0.0, 0.0, 1.0]},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertTrue(res.success)
        self.assertEqual(res.tool, "create_primitive")
        self.assertEqual(res.data["object_name"], "Cube")
        self.mock_adapter.create_primitive.assert_called_once_with(
            primitive_type="CUBE",
            name=None,
            location=[0.0, 0.0, 1.0],
            rotation=None,
            scale=None,
            size=None,
        )

    def test_create_primitive_missing_required_type(self):
        tc = ToolCall(
            call_id="call_2",
            tool_name="create_primitive",
            arguments={"name": "TestObj"},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("primitive_type", res.error.message)
        self.mock_adapter.create_primitive.assert_not_called()

    def test_create_primitive_unexpected_argument(self):
        tc = ToolCall(
            call_id="call_3",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "invalid_param": 123},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("unexpected", res.error.message.lower())

    def test_transform_object_dispatch_success(self):
        self.mock_adapter.transform_object.return_value = ToolResult.ok(
            "transform_object",
            {"object_name": "Cube", "changed": ["location"]},
        )

        tc = ToolCall(
            call_id="call_4",
            tool_name="transform_object",
            arguments={"name": "Cube", "location": [1.0, 2.0, 3.0]},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertTrue(res.success)
        self.mock_adapter.transform_object.assert_called_once_with(
            name="Cube",
            location=[1.0, 2.0, 3.0],
            rotation=None,
            scale=None,
            relative=False,
        )

    def test_transform_object_no_transform_fields_fails(self):
        tc = ToolCall(
            call_id="call_5",
            tool_name="transform_object",
            arguments={"name": "Cube"},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("at least one transform property", res.error.message.lower())
        self.mock_adapter.transform_object.assert_not_called()

    def test_delete_object_dispatch_success(self):
        self.mock_adapter.delete_object.return_value = ToolResult.ok(
            "delete_object",
            {"deleted": True, "object_name": "Cube"},
        )

        tc = ToolCall(
            call_id="call_6",
            tool_name="delete_object",
            arguments={"name": "Cube"},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertTrue(res.success)
        self.mock_adapter.delete_object.assert_called_once_with(name="Cube")

    def test_delete_object_missing_name_fails(self):
        tc = ToolCall(
            call_id="call_7",
            tool_name="delete_object",
            arguments={},
        )
        res = self.dispatcher.dispatch(tc)

        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("name", res.error.message)
        self.mock_adapter.delete_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
