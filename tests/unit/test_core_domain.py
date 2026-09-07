"""Unit tests for Phase 2 Core Domain & Tool Foundation.

Pure Python tests with ZERO Blender dependencies.
"""

import unittest
from typing import Any

from core.types import RiskLevel, ToolError, ToolResult
from tools.base import BaseTool, InvalidToolContractError
from tools.registry import ToolAlreadyRegisteredError, ToolNotFoundError, ToolRegistry


class DummyValidTool(BaseTool):
    """A valid concrete tool for testing."""

    name = "dummy_inspect"
    description = "A dummy inspection tool for testing."
    input_schema = {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        target = kwargs.get("target", "default")
        return ToolResult.ok(self.name, {"inspected": target})


class DummyMutationTool(BaseTool):
    """A dummy mutation tool with LOW risk."""

    name = "dummy_create"
    description = "A dummy creation tool for testing."
    input_schema = {"type": "object"}
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, {"created": True})


class DummyZTool(BaseTool):
    name = "z_alpha_tool"
    description = "Tool Z"
    input_schema = {}
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, {})


class DummyATool(BaseTool):
    name = "a_beta_tool"
    description = "Tool A"
    input_schema = {}
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, {})


class TestCoreDomain(unittest.TestCase):
    """Test suite covering types, contracts, and registry."""

    # 1. ToolResult Serialization Tests
    def test_tool_result_ok_serialization(self):
        result = ToolResult.ok("test_tool", {"cube_count": 3})
        as_dict = result.to_dict()

        self.assertTrue(as_dict["success"])
        self.assertEqual(as_dict["tool"], "test_tool")
        self.assertEqual(as_dict["data"], {"cube_count": 3})
        self.assertIsNone(as_dict["error"])

    def test_tool_result_fail_serialization(self):
        result = ToolResult.fail(
            tool="test_tool",
            error_type="OBJECT_NOT_FOUND",
            message="Object 'Ghost' does not exist.",
            details={"queried": "Ghost", "count": 0},
        )
        as_dict = result.to_dict()

        self.assertFalse(as_dict["success"])
        self.assertEqual(as_dict["tool"], "test_tool")
        self.assertIsNone(as_dict["data"])
        self.assertIsNotNone(as_dict["error"])
        self.assertEqual(as_dict["error"]["type"], "OBJECT_NOT_FOUND")
        self.assertEqual(as_dict["error"]["message"], "Object 'Ghost' does not exist.")
        self.assertEqual(as_dict["error"]["details"], {"count": 0, "queried": "Ghost"})

    # 2. RiskLevel Validation
    def test_risk_level_values(self):
        self.assertEqual(RiskLevel.READ_ONLY.value, "READ_ONLY")
        self.assertEqual(RiskLevel.LOW.value, "LOW")
        self.assertEqual(RiskLevel.MEDIUM.value, "MEDIUM")
        self.assertEqual(RiskLevel.HIGH.value, "HIGH")
        self.assertEqual(RiskLevel.CRITICAL.value, "CRITICAL")

    # 3. Invalid Tool Contract Tests
    def test_invalid_tool_missing_name(self):
        with self.assertRaises(InvalidToolContractError):
            class BrokenTool1(BaseTool):
                description = "Missing name"
                input_schema = {}
                risk_level = RiskLevel.READ_ONLY

                def execute(self, adapter, **kwargs):
                    return ToolResult.ok("broken", {})

    def test_invalid_tool_missing_description(self):
        with self.assertRaises(InvalidToolContractError):
            class BrokenTool2(BaseTool):
                name = "broken_tool_2"
                input_schema = {}
                risk_level = RiskLevel.READ_ONLY

                def execute(self, adapter, **kwargs):
                    return ToolResult.ok(self.name, {})

    def test_invalid_tool_missing_schema(self):
        with self.assertRaises(InvalidToolContractError):
            class BrokenTool3(BaseTool):
                name = "broken_tool_3"
                description = "Missing schema"
                input_schema = "not_a_dict"
                risk_level = RiskLevel.READ_ONLY

                def execute(self, adapter, **kwargs):
                    return ToolResult.ok(self.name, {})

    def test_invalid_tool_invalid_risk_level(self):
        with self.assertRaises(InvalidToolContractError):
            class BrokenTool4(BaseTool):
                name = "broken_tool_4"
                description = "Invalid risk level"
                input_schema = {}
                risk_level = "SUPER_HIGH"  # Not a RiskLevel enum

                def execute(self, adapter, **kwargs):
                    return ToolResult.ok(self.name, {})

    # 4. Valid Tool Execution and Schema Export
    def test_valid_tool_execution(self):
        tool = DummyValidTool()
        result = tool.execute(adapter=None, target="SceneCube")
        self.assertTrue(result.success)
        self.assertEqual(result.data["inspected"], "SceneCube")

    def test_valid_tool_schema_export(self):
        tool = DummyValidTool()
        schema = tool.to_schema()
        self.assertEqual(schema["name"], "dummy_inspect")
        self.assertEqual(schema["description"], "A dummy inspection tool for testing.")
        self.assertEqual(schema["risk_level"], "READ_ONLY")
        self.assertIn("properties", schema["input_schema"])

    # 5. Registry Tests
    def test_registry_register_and_get(self):
        registry = ToolRegistry()
        tool = DummyValidTool()

        self.assertFalse(registry.exists("dummy_inspect"))
        registry.register(tool)
        self.assertTrue(registry.exists("dummy_inspect"))
        self.assertEqual(registry.get("dummy_inspect"), tool)
        self.assertEqual(len(registry), 1)

    def test_registry_duplicate_register_error(self):
        registry = ToolRegistry()
        tool1 = DummyValidTool()
        tool2 = DummyValidTool()

        registry.register(tool1)
        with self.assertRaises(ToolAlreadyRegisteredError):
            registry.register(tool2)

    def test_registry_get_missing_error(self):
        registry = ToolRegistry()
        with self.assertRaises(ToolNotFoundError):
            registry.get("non_existent_tool")

    def test_registry_unregister(self):
        registry = ToolRegistry()
        tool = DummyValidTool()
        registry.register(tool)
        self.assertTrue(registry.exists("dummy_inspect"))

        registry.unregister("dummy_inspect")
        self.assertFalse(registry.exists("dummy_inspect"))

        with self.assertRaises(ToolNotFoundError):
            registry.unregister("dummy_inspect")

    def test_registry_deterministic_ordering(self):
        registry = ToolRegistry()
        # Register in reverse/random alphabetical order
        registry.register(DummyZTool())
        registry.register(DummyMutationTool())
        registry.register(DummyATool())
        registry.register(DummyValidTool())

        tools = registry.list()
        names = [t.name for t in tools]
        # Must be strictly sorted alphabetically
        expected_order = sorted(["z_alpha_tool", "dummy_create", "a_beta_tool", "dummy_inspect"])
        self.assertEqual(names, expected_order)

        # Exported schemas must also follow strict alphabetical order
        schemas = registry.export_schemas()
        schema_names = [s["name"] for s in schemas]
        self.assertEqual(schema_names, expected_order)


if __name__ == "__main__":
    unittest.main()
