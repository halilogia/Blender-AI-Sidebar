"""Unit tests for M4.2 Task 3 ProposePlanTool meta-tool.

Tests:
1. Valid plan proposal (single step)
2. Valid multi-step proposal with topological ordering
3. Invalid JSON handling (raw_plan / steps / arguments)
4. Missing required fields (title, steps)
5. Unknown tool rejection
6. propose_plan recursion attempt rejection
7. Invalid arguments rejection
8. Dependency cycle rejection
9. Missing dependency rejection
10. Fake overall risk ignored/recalculated from ToolRegistry
11. Topological ordering applied to plan steps
12. Conversation sequence validity (ASSISTANT -> TOOL)
13. Zero Blender mutation execution (adapter mutations untouched)
14. Zero bpy dependency verification (AST inspection)
15. ToolRegistry auto-wiring on registration
16. ToolDispatcher integration
"""

import ast
import json
import os
import unittest
from unittest.mock import MagicMock

from core.types import RiskLevel, ToolResult
from agent.models import ChatMessage, Conversation, Role, ToolCall
from agent.dispatcher import ToolDispatcher
from tools.base import BaseTool
from tools.registry import ToolRegistry
from tools.propose_plan import ProposePlanTool
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.delete_object import DeleteObjectTool
from tools.mutations.transform_object import TransformObjectTool


class DummyInspectTool(BaseTool):
    name = "dummy_inspect"
    description = "Read-only inspection tool"
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
        "additionalProperties": False,
    }

    def execute(self, adapter, **kwargs):
        return ToolResult.ok(tool=self.name, data={})


class TestProposePlanTool(unittest.TestCase):
    """Comprehensive test suite for ProposePlanTool."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(DeleteObjectTool())
        self.registry.register(TransformObjectTool())
        self.registry.register(DummyInspectTool())

        self.tool = ProposePlanTool(registry=self.registry)
        self.registry.register(self.tool)

        # Mock adapter tracking mutation calls
        self.adapter = MagicMock()

    # 1. Valid single-step proposal
    def test_valid_single_step_proposal(self):
        """A single-step valid plan successfully validates and returns structured plan data."""
        payload = {
            "title": "Create Cube Plan",
            "description": "Create a simple cube at the scene origin",
            "steps": [
                {
                    "step_id": "step_1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE", "size": 2.0},
                    "description": "Spawn base cube",
                    "expected_result": "Cube at origin",
                }
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertTrue(result.success, f"Failed with: {result.error}")
        self.assertEqual(result.tool, "propose_plan")
        data = result.data
        self.assertEqual(data["status"], "VALIDATED")
        self.assertEqual(data["overall_risk"], "LOW")
        self.assertEqual(data["topological_order"], ["step_1"])
        self.assertEqual(data["steps_count"], 1)
        self.assertIn("Create Cube Plan", data["summary"])
        self.assertEqual(data["plan"]["title"], "Create Cube Plan")

    # 2. Valid multi-step proposal
    def test_valid_multi_step_proposal(self):
        """Multi-step proposal with sequential dependencies validates properly."""
        payload = {
            "title": "Build and Move",
            "description": "Create cube and transform position",
            "steps": [
                {
                    "step_id": "step_create",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "step_transform",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube", "location": [0.0, 0.0, 5.0]},
                    "depends_on": ["step_create"],
                },
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertTrue(result.success)
        data = result.data
        self.assertEqual(data["topological_order"], ["step_create", "step_transform"])
        self.assertEqual(data["steps_count"], 2)
        self.assertEqual(data["overall_risk"], "LOW")

    # 3. Invalid JSON handling
    def test_invalid_json_proposal(self):
        """Malformed JSON string passed in raw_plan or steps must fail safely."""
        result = self.tool.execute(self.adapter, raw_plan="Not a valid json {{{{")
        self.assertFalse(result.success)
        self.assertEqual(result.error.type, "INVALID_JSON")

        result2 = self.tool.execute(self.adapter, title="Test", steps="malformed steps {[[")
        self.assertFalse(result2.success)
        self.assertEqual(result2.error.type, "INVALID_JSON")

        result3 = self.tool.execute(
            self.adapter,
            title="Bad Step Args",
            steps=[{"step_id": "s1", "tool_name": "create_primitive", "arguments": "bad json {{"}],
        )
        self.assertFalse(result3.success)
        self.assertEqual(result3.error.type, "INVALID_JSON")

    # 4. Missing required fields
    def test_missing_required_fields(self):
        """Missing title, missing steps, or empty steps array must be rejected."""
        # Missing title
        res_no_title = self.tool.execute(
            self.adapter,
            steps=[{"step_id": "s1", "tool_name": "create_primitive", "arguments": {"primitive_type": "CUBE"}}],
        )
        self.assertFalse(res_no_title.success)
        self.assertEqual(res_no_title.error.type, "MISSING_REQUIRED_FIELD")
        self.assertIn("title", res_no_title.error.message)

        # Missing steps
        res_no_steps = self.tool.execute(self.adapter, title="Valid Title")
        self.assertFalse(res_no_steps.success)
        self.assertEqual(res_no_steps.error.type, "MISSING_REQUIRED_FIELD")
        self.assertIn("steps", res_no_steps.error.message)

        # Empty steps
        res_empty_steps = self.tool.execute(self.adapter, title="Valid Title", steps=[])
        self.assertFalse(res_empty_steps.success)
        self.assertEqual(res_empty_steps.error.type, "EMPTY_PLAN")

    # 5. Unknown tool rejection
    def test_unknown_tool_rejected(self):
        """Plan containing tool not present in ToolRegistry must be rejected."""
        payload = {
            "title": "Unknown Tool Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "unknown_blender_magic",
                    "arguments": {},
                }
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertFalse(result.success)
        self.assertEqual(result.error.type, "PLAN_VALIDATION_FAILED")
        self.assertIn("Unknown tool 'unknown_blender_magic'", result.error.message)

    # 6. propose_plan recursion attempt rejection
    def test_propose_plan_recursion_attempt_rejected(self):
        """Nesting propose_plan inside an execution plan must be strictly forbidden."""
        payload = {
            "title": "Recursive Plan",
            "steps": [
                {
                    "step_id": "step_meta",
                    "tool_name": "propose_plan",
                    "arguments": {"title": "Subplan", "steps": []},
                }
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertFalse(result.success)
        self.assertEqual(result.error.type, "PLAN_VALIDATION_FAILED")
        self.assertIn("propose_plan", result.error.message)
        self.assertIn("cannot be nested", result.error.message)

    # 7. Invalid arguments rejection
    def test_invalid_arguments_rejected(self):
        """Invalid tool arguments must cause validation to fail."""
        payload = {
            "title": "Invalid Arguments Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "INVALID_PRIMITIVE"},  # Invalid enum
                }
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertFalse(result.success)
        self.assertEqual(result.error.type, "PLAN_VALIDATION_FAILED")
        self.assertIn("not in allowed enum", result.error.message)

    # 8. Dependency cycle rejection
    def test_dependency_cycle_rejected(self):
        """Plan with circular dependency (s1 -> s2 -> s1) must be rejected."""
        payload = {
            "title": "Cycle Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": ["s2"],
                },
                {
                    "step_id": "s2",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "SPHERE"},
                    "depends_on": ["s1"],
                },
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertFalse(result.success)
        self.assertEqual(result.error.type, "PLAN_VALIDATION_FAILED")
        self.assertIn("Dependency cycle detected", result.error.message)

    # 9. Missing dependency rejection
    def test_missing_dependency_rejected(self):
        """Step depending on a non-existent step_id must be rejected."""
        payload = {
            "title": "Missing Dep Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": ["non_existent_step_42"],
                }
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertFalse(result.success)
        self.assertEqual(result.error.type, "PLAN_VALIDATION_FAILED")
        self.assertIn("non-existent step 'non_existent_step_42'", result.error.message)

    # 10. Fake overall risk ignored and recalculated
    def test_fake_overall_risk_ignored_and_recalculated(self):
        """LLM asserting low or fake risk for a medium/high tool must be recalculated."""
        # LLM claims READ_ONLY, but delete_object is MEDIUM
        payload = {
            "title": "Deceptive Risk Proposal",
            "overall_risk": "READ_ONLY",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "delete_object",
                    "arguments": {"name": "ObsoleteMesh"},
                }
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertTrue(result.success)
        # Authority derived strictly from ToolRegistry: delete_object is MEDIUM
        self.assertEqual(result.data["overall_risk"], "MEDIUM")
        self.assertEqual(result.data["plan"]["overall_risk"], "MEDIUM")

        # LLM claims CRITICAL, but tool is create_primitive (LOW)
        payload2 = {
            "title": "Inflated Risk Proposal",
            "overall_risk": "CRITICAL",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                }
            ],
        }
        result2 = self.tool.execute(self.adapter, **payload2)
        self.assertTrue(result2.success)
        self.assertEqual(result2.data["overall_risk"], "LOW")

    # 11. Topological ordering applied
    def test_topological_ordering_applied(self):
        """Forward dependency declaration is topologically sorted in output plan."""
        # s1 declared first, depends on s2 declared second
        payload = {
            "title": "Reordering Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s2"],
                },
                {
                    "step_id": "s2",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertTrue(result.success)
        data = result.data
        self.assertEqual(data["topological_order"], ["s2", "s1"])
        self.assertEqual(data["plan"]["steps"][0]["step_id"], "s2")
        self.assertEqual(data["plan"]["steps"][1]["step_id"], "s1")

    # 12. Conversation sequence validity
    def test_conversation_sequence_validity(self):
        """ASSISTANT(tool_calls=[propose_plan]) -> TOOL(propose_plan) satisfies sequence validation."""
        conv = Conversation()
        conv.add_message(ChatMessage(role=Role.USER, content="Create a chair and a table"))

        tool_args = {
            "title": "Create Furniture",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                }
            ],
        }

        # Execute tool to get valid response
        result = self.tool.execute(self.adapter, **tool_args)
        self.assertTrue(result.success)

        # Assistant emits propose_plan tool call
        conv.add_message(
            ChatMessage(
                role=Role.ASSISTANT,
                content="I propose a plan to build the furniture.",
                tool_calls=[
                    ToolCall(
                        call_id="call_plan_001",
                        tool_name="propose_plan",
                        arguments=tool_args,
                    )
                ],
            )
        )

        # Tool returns proposal result
        conv.add_message(
            ChatMessage(
                role=Role.TOOL,
                tool_call_id="call_plan_001",
                content=json.dumps(result.to_dict()),
                name="propose_plan",
            )
        )

        # Sequence validation must succeed without exception
        conv.validate_sequence()
        self.assertEqual(len(conv.messages), 3)

    # 13. Proposal does not execute Blender mutations
    def test_proposal_does_not_execute_blender_mutation(self):
        """Executing propose_plan MUST NOT call any mutation methods on the adapter."""
        payload = {
            "title": "Mutation Free Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "s2",
                    "tool_name": "delete_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s1"],
                },
            ],
        }

        result = self.tool.execute(self.adapter, **payload)
        self.assertTrue(result.success)

        # Assert no mutation methods were called on the adapter
        self.adapter.create_primitive.assert_not_called()
        self.adapter.delete_object.assert_not_called()
        self.adapter.transform_object.assert_not_called()

    # 14. Zero bpy dependency verification (AST inspection)
    def test_no_bpy_dependency(self):
        """tools/propose_plan.py must not import or reference bpy."""
        file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "tools", "propose_plan.py")
        )
        with open(file_path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=file_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "bpy", f"Forbidden 'import bpy' in {file_path}")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "bpy", f"Forbidden 'from bpy ...' in {file_path}")
                if node.module:
                    self.assertFalse(node.module.startswith("bpy."), f"Forbidden 'from bpy... in {file_path}")

    # 15. ToolRegistry auto-wiring on registration
    def test_tool_registry_auto_wiring(self):
        """Registering ProposePlanTool in ToolRegistry automatically sets registry."""
        fresh_registry = ToolRegistry()
        fresh_registry.register(CreatePrimitiveTool())

        tool_unwired = ProposePlanTool()
        self.assertIsNone(tool_unwired.registry)

        fresh_registry.register(tool_unwired)
        self.assertIs(tool_unwired.registry, fresh_registry)

    # 16. ToolDispatcher integration
    def test_tool_dispatcher_integration(self):
        """ToolDispatcher successfully dispatches propose_plan calls."""
        dispatcher = ToolDispatcher(registry=self.registry, adapter=self.adapter)

        tool_call = ToolCall(
            call_id="call_dispatch_1",
            tool_name="propose_plan",
            arguments={
                "title": "Dispatched Plan",
                "steps": [
                    {
                        "step_id": "s1",
                        "tool_name": "create_primitive",
                        "arguments": {"primitive_type": "SPHERE"},
                    }
                ],
            },
        )

        res = dispatcher.dispatch(tool_call)
        self.assertTrue(res.success)
        self.assertEqual(res.tool, "propose_plan")
        self.assertEqual(res.data["status"], "VALIDATED")
        self.assertEqual(res.data["plan"]["title"], "Dispatched Plan")


if __name__ == "__main__":
    unittest.main()
