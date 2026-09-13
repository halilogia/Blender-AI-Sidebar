"""Unit tests for M4.2 Task 4 Plan Execution Engine.

Tests:
1. Single step successful execution
2. Multi-step successful execution
3. Dependency ordering enforced (topological order)
4. Dependency failure skips subsequent dependent steps (fail-fast)
5. Unknown tool rejected before execution
6. propose_plan step rejected (recursion prevention)
7. Failed ToolResult handled and recorded
8. Execution summary structure and serialization
9. Step results exact ordering
10. Real ToolDispatcher / ToolRegistry integration
11. Mutation tool actually called on adapter
12. Validation failure before any execution occurs
13. Semantic verification outcome preserved and not overwritten
14. Approval boundary and policy evaluation
"""

import ast
import os
import unittest
from unittest.mock import MagicMock

from core.types import RiskLevel, ToolError, ToolResult
from agent.models import ToolCall
from agent.plan_models import (
    Plan,
    PlanStep,
    PlanStepStatus,
    PlanStatus,
    PlanStepExecutionResult,
    PlanExecutionSummary,
)
from agent.plan_executor import PlanExecutor
from agent.policy import ApprovalDecision, ApprovalPolicy
from agent.dispatcher import ToolDispatcher
from tools.base import BaseTool
from tools.registry import ToolRegistry
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
        return ToolResult.ok(tool=self.name, data={"target": kwargs.get("target")})


class TestPlanExecutor(unittest.TestCase):
    """Comprehensive test suite for PlanExecutor."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(DeleteObjectTool())
        self.registry.register(TransformObjectTool())
        self.registry.register(DummyInspectTool())

        self.adapter = MagicMock()
        # Mock default return values for adapter mutation methods
        self.adapter.create_primitive.return_value = ToolResult.ok(
            tool="create_primitive", data={"name": "Cube", "created": True}
        )
        self.adapter.transform_object.return_value = ToolResult.ok(
            tool="transform_object", data={"name": "Cube", "transformed": True}
        )
        self.adapter.delete_object.return_value = ToolResult.ok(
            tool="delete_object", data={"name": "Cube", "deleted": True}
        )

        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.adapter)
        self.executor = PlanExecutor(
            registry=self.registry,
            dispatcher=self.dispatcher,
        )

    # 1. Single step successful execution
    def test_single_step_successful_execution(self):
        """Single valid step executes, calls adapter, and returns COMPLETED summary."""
        raw_plan = {
            "title": "Create Cube",
            "steps": [
                {
                    "step_id": "step_1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE", "size": 2.0},
                }
            ],
        }

        summary = self.executor.execute_plan(raw_plan)

        self.assertEqual(summary.status, PlanStatus.COMPLETED)
        self.assertEqual(summary.steps_total, 1)
        self.assertEqual(summary.steps_completed, 1)
        self.assertIsNone(summary.failure_reason)
        self.assertEqual(len(summary.step_results), 1)

        step_res = summary.step_results[0]
        self.assertEqual(step_res.step_id, "step_1")
        self.assertEqual(step_res.status, PlanStepStatus.COMPLETED)
        self.assertIsNotNone(step_res.tool_result)
        self.assertTrue(step_res.tool_result.success)
        self.assertGreaterEqual(step_res.duration_seconds, 0.0)

        # Verify adapter was actually called
        self.adapter.create_primitive.assert_called_once()
        _, call_kwargs = self.adapter.create_primitive.call_args
        self.assertEqual(call_kwargs["primitive_type"], "CUBE")
        self.assertEqual(call_kwargs["size"], 2.0)

    # 2. Multi-step successful execution
    def test_multi_step_successful_execution(self):
        """Multi-step plan executes all steps in sequence and returns COMPLETED."""
        raw_plan = {
            "title": "Create and Move",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "s2",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube", "location": [1.0, 2.0, 3.0]},
                    "depends_on": ["s1"],
                },
            ],
        }

        summary = self.executor.execute_plan(raw_plan)

        self.assertEqual(summary.status, PlanStatus.COMPLETED)
        self.assertEqual(summary.steps_total, 2)
        self.assertEqual(summary.steps_completed, 2)
        self.assertEqual(summary.step_results[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(summary.step_results[1].status, PlanStepStatus.COMPLETED)

        self.adapter.create_primitive.assert_called_once()
        _, call_kwargs = self.adapter.create_primitive.call_args
        self.assertEqual(call_kwargs["primitive_type"], "CUBE")
        self.adapter.transform_object.assert_called_once()
        _, call_kwargs_transform = self.adapter.transform_object.call_args
        self.assertEqual(call_kwargs_transform["name"], "Cube")
        self.assertEqual(list(call_kwargs_transform["location"]), [1.0, 2.0, 3.0])

    # 3. Dependency ordering enforced (topological order)
    def test_dependency_ordering_enforced(self):
        """Steps declared out of order are executed in topological order."""
        call_order = []
        self.adapter.create_primitive.side_effect = lambda **kwargs: call_order.append("create") or ToolResult.ok(
            tool="create_primitive", data={}
        )
        self.adapter.transform_object.side_effect = lambda **kwargs: call_order.append("transform") or ToolResult.ok(
            tool="transform_object", data={}
        )

        # Declare transform (s1) first, but it depends on create (s2) declared second
        raw_plan = {
            "title": "Forward Dependency Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube", "location": [0.0, 1.0, 0.0]},
                    "depends_on": ["s2"],
                },
                {
                    "step_id": "s2",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
            ],
        }

        summary = self.executor.execute_plan(raw_plan)

        self.assertEqual(summary.status, PlanStatus.COMPLETED)
        # s2 must execute before s1
        self.assertEqual(call_order, ["create", "transform"])
        self.assertEqual(summary.step_results[0].step_id, "s2")
        self.assertEqual(summary.step_results[1].step_id, "s1")

    # 4. Dependency failure skips subsequent dependent steps (fail-fast)
    def test_dependency_failure_skips_subsequent_steps(self):
        """When a step fails, subsequent dependent steps are marked SKIPPED and not executed."""
        # Make step 1 fail
        self.adapter.create_primitive.return_value = ToolResult.fail(
            tool="create_primitive",
            error_type="OUT_OF_MEMORY",
            message="Mesh creation failed due to memory limit",
        )

        raw_plan = {
            "title": "Fail Fast Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "s2",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s1"],
                },
                {
                    "step_id": "s3",
                    "tool_name": "delete_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s2"],
                },
            ],
        }

        summary = self.executor.execute_plan(raw_plan)

        self.assertEqual(summary.status, PlanStatus.FAILED)
        self.assertEqual(summary.steps_total, 3)
        self.assertEqual(summary.steps_completed, 0)
        self.assertIn("s1", summary.failure_reason)

        # Step 1 failed
        self.assertEqual(summary.step_results[0].step_id, "s1")
        self.assertEqual(summary.step_results[0].status, PlanStepStatus.FAILED)
        self.assertIn("Mesh creation failed", summary.step_results[0].error_message)

        # Steps 2 and 3 skipped
        self.assertEqual(summary.step_results[1].step_id, "s2")
        self.assertEqual(summary.step_results[1].status, PlanStepStatus.SKIPPED)
        self.assertEqual(summary.step_results[2].step_id, "s3")
        self.assertEqual(summary.step_results[2].status, PlanStepStatus.SKIPPED)

        # Adapter transform and delete must never have been called
        self.adapter.transform_object.assert_not_called()
        self.adapter.delete_object.assert_not_called()

    # 5. Unknown tool rejection
    def test_unknown_tool_rejected(self):
        """Plan containing unknown tool fails validation before any steps are executed."""
        raw_plan = {
            "title": "Bad Tool Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "s2",
                    "tool_name": "non_existent_tool",
                    "arguments": {},
                },
            ],
        }

        summary = self.executor.execute_plan(raw_plan)

        self.assertEqual(summary.status, PlanStatus.FAILED)
        self.assertEqual(summary.steps_completed, 0)
        self.assertIn("Unknown tool 'non_existent_tool'", summary.failure_reason)
        # Zero steps executed
        self.adapter.create_primitive.assert_not_called()

    # 6. propose_plan step rejected (recursion prevention)
    def test_propose_plan_step_rejected(self):
        """Plan referencing propose_plan as a step is rejected without execution."""
        raw_plan = {
            "title": "Recursive Execution Attempt",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "propose_plan",
                    "arguments": {"title": "Nested"},
                }
            ],
        }

        summary = self.executor.execute_plan(raw_plan)

        self.assertEqual(summary.status, PlanStatus.FAILED)
        self.assertEqual(summary.steps_completed, 0)
        self.assertIn("propose_plan", summary.failure_reason)

    # 7. Failed ToolResult handled and recorded
    def test_failed_tool_result_handled(self):
        """Dispatcher returning a ToolResult.fail is cleanly captured in PlanStepExecutionResult."""
        self.adapter.delete_object.return_value = ToolResult.fail(
            tool="delete_object",
            error_type="OBJECT_NOT_FOUND",
            message="Object 'MissingObj' does not exist in scene.",
            details={"name": "MissingObj"},
        )

        raw_plan = {
            "title": "Delete Missing",
            "steps": [
                {
                    "step_id": "del_step",
                    "tool_name": "delete_object",
                    "arguments": {"name": "MissingObj"},
                }
            ],
        }

        summary = self.executor.execute_plan(raw_plan)

        self.assertEqual(summary.status, PlanStatus.FAILED)
        self.assertEqual(summary.steps_completed, 0)
        res = summary.step_results[0]
        self.assertEqual(res.status, PlanStepStatus.FAILED)
        self.assertEqual(res.error_message, "Object 'MissingObj' does not exist in scene.")
        self.assertFalse(res.tool_result.success)
        self.assertEqual(res.tool_result.error.type, "OBJECT_NOT_FOUND")

    # 8. Execution summary structure and serialization
    def test_execution_summary_structure_and_serialization(self):
        """PlanExecutionSummary to_dict and from_dict roundtrip preserve exact fields."""
        raw_plan = {
            "title": "Serialize Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "SPHERE"},
                }
            ],
        }

        summary = self.executor.execute_plan(raw_plan)
        d = summary.to_dict()

        self.assertEqual(d["status"], "COMPLETED")
        self.assertEqual(d["steps_total"], 1)
        self.assertEqual(d["steps_completed"], 1)
        self.assertEqual(len(d["step_results"]), 1)
        self.assertEqual(d["step_results"][0]["step_id"], "s1")
        self.assertEqual(d["step_results"][0]["status"], "COMPLETED")

        reconstructed = PlanExecutionSummary.from_dict(d)
        self.assertEqual(reconstructed.status, PlanStatus.COMPLETED)
        self.assertEqual(reconstructed.steps_completed, 1)
        self.assertEqual(reconstructed.step_results[0].step_id, "s1")

    # 9. Step results exact ordering
    def test_step_results_exact_ordering(self):
        """PlanExecutionSummary step_results preserves exact topological execution sequence."""
        raw_plan = {
            "title": "Three Step Pipeline",
            "steps": [
                {
                    "step_id": "step_a",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "step_b",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube", "location": [0.0, 1.0, 0.0]},
                    "depends_on": ["step_a"],
                },
                {
                    "step_id": "step_c",
                    "tool_name": "delete_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["step_b"],
                },
            ],
        }

        summary = self.executor.execute_plan(raw_plan)
        self.assertEqual(summary.status, PlanStatus.COMPLETED)
        result_ids = [sr.step_id for sr in summary.step_results]
        self.assertEqual(result_ids, ["step_a", "step_b", "step_c"])

    # 10. Real ToolDispatcher / ToolRegistry integration
    def test_real_dispatcher_tool_registry_integration(self):
        """PlanExecutor integrates directly with ToolDispatcher without duplicating dispatch logic."""
        self.assertIs(self.executor.dispatcher, self.dispatcher)
        self.assertIs(self.executor.registry, self.registry)

        raw_plan = {
            "title": "Dispatch Integration",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "dummy_inspect",
                    "arguments": {"target": "Scene"},
                }
            ],
        }

        summary = self.executor.execute_plan(raw_plan)
        self.assertEqual(summary.status, PlanStatus.COMPLETED)
        self.assertEqual(summary.step_results[0].tool_result.data["target"], "Scene")

    # 11. Mutation tool actually called on adapter
    def test_mutation_tool_actually_called(self):
        """Adapter mutation methods are executed with exact parameters."""
        raw_plan = {
            "title": "Mutation Verification",
            "steps": [
                {
                    "step_id": "create_step",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "PLANE", "size": 5.0},
                }
            ],
        }

        summary = self.executor.execute_plan(raw_plan)
        self.assertEqual(summary.status, PlanStatus.COMPLETED)
        self.adapter.create_primitive.assert_called_once()
        _, call_kwargs = self.adapter.create_primitive.call_args
        self.assertEqual(call_kwargs["primitive_type"], "PLANE")
        self.assertEqual(call_kwargs["size"], 5.0)

    # 12. Validation failure before any execution occurs
    def test_validation_failure_before_execution(self):
        """Cycle or malformed data causes immediate failure without running any adapter methods."""
        cycle_plan = {
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

        summary = self.executor.execute_plan(cycle_plan)
        self.assertEqual(summary.status, PlanStatus.FAILED)
        self.assertEqual(summary.steps_completed, 0)
        self.assertIn("cycle", summary.failure_reason.lower())
        self.adapter.create_primitive.assert_not_called()

    # 13. Semantic verification outcome preserved and not overwritten
    def test_semantic_verification_preserved(self):
        """When semantic verifier is attached, verification data is preserved in tool result."""
        mock_verifier = MagicMock()
        mock_verif_result = MagicMock()
        mock_verif_result.passed = True
        mock_verif_result.summary = "Object verified in scene"
        mock_verif_result.to_dict.return_value = {"passed": True, "object_verified": "Cube"}
        mock_verifier.verify.return_value = mock_verif_result

        executor_with_verif = PlanExecutor(
            registry=self.registry,
            dispatcher=self.dispatcher,
            verifier=mock_verifier,
        )

        raw_plan = {
            "title": "Verified Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                }
            ],
        }

        summary = executor_with_verif.execute_plan(raw_plan)
        self.assertEqual(summary.status, PlanStatus.COMPLETED)

        step_res = summary.step_results[0]
        self.assertIn("verification", step_res.tool_result.data)
        self.assertTrue(step_res.tool_result.data["verification"]["passed"])

    def test_semantic_verification_failure_halts_plan(self):
        """When semantic verifier reports failure, step fails and plan halts."""
        mock_verifier = MagicMock()
        mock_verif_result = MagicMock()
        mock_verif_result.passed = False
        mock_verif_result.summary = "Object was not created"
        mock_verif_result.mismatches = ["exists expected True, got False"]
        mock_verif_result.to_dict.return_value = {"passed": False}
        mock_verifier.verify.return_value = mock_verif_result

        executor_with_verif = PlanExecutor(
            registry=self.registry,
            dispatcher=self.dispatcher,
            verifier=mock_verifier,
        )

        raw_plan = {
            "title": "Failed Verification Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "s2",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s1"],
                },
            ],
        }

        summary = executor_with_verif.execute_plan(raw_plan)
        self.assertEqual(summary.status, PlanStatus.FAILED)
        self.assertEqual(summary.step_results[0].status, PlanStepStatus.FAILED)
        self.assertEqual(summary.step_results[1].status, PlanStepStatus.SKIPPED)

    # 14. Approval boundary and policy evaluation
    def test_approval_boundary_rejection_and_approval(self):
        """Approval policy boundary gates medium/high risk steps via approval_hook."""
        policy = ApprovalPolicy()

        # Hook rejecting delete_object (MEDIUM risk)
        approval_hook_reject = MagicMock(return_value=False)
        executor_gated = PlanExecutor(
            registry=self.registry,
            dispatcher=self.dispatcher,
            policy=policy,
            approval_hook=approval_hook_reject,
        )

        raw_plan = {
            "title": "Delete Gate Plan",
            "steps": [
                {
                    "step_id": "del_1",
                    "tool_name": "delete_object",
                    "arguments": {"name": "Cube"},
                }
            ],
        }

        summary_rejected = executor_gated.execute_plan(raw_plan)
        self.assertEqual(summary_rejected.status, PlanStatus.FAILED)
        self.assertEqual(summary_rejected.step_results[0].status, PlanStepStatus.CANCELLED)
        self.adapter.delete_object.assert_not_called()
        approval_hook_reject.assert_called_once()

        # Hook approving delete_object
        approval_hook_approve = MagicMock(return_value=True)
        executor_approved = PlanExecutor(
            registry=self.registry,
            dispatcher=self.dispatcher,
            policy=policy,
            approval_hook=approval_hook_approve,
        )

        summary_approved = executor_approved.execute_plan(raw_plan)
        self.assertEqual(summary_approved.status, PlanStatus.COMPLETED)
        self.assertEqual(summary_approved.step_results[0].status, PlanStepStatus.COMPLETED)
        self.adapter.delete_object.assert_called_once()

    # 16. AgentRuntime.execute_plan integration
    def test_agent_runtime_execute_plan_integration(self):
        """AgentRuntime delegates to PlanExecutor cleanly without state machine corruption."""
        from agent.runtime import AgentRuntime

        mock_provider = MagicMock()
        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=self.dispatcher,
            verifier=False,
        )

        raw_plan = {
            "title": "Runtime Plan",
            "steps": [
                {
                    "step_id": "rt_s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                }
            ],
        }

        summary = runtime.execute_plan(raw_plan)
        self.assertEqual(summary.status, PlanStatus.COMPLETED)
        self.assertEqual(summary.steps_completed, 1)
        self.adapter.create_primitive.assert_called_once()

    # 17. Zero bpy dependency verification (AST inspection)
    def test_no_bpy_dependency(self):
        """agent/plan_executor.py must not import or reference bpy."""
        file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "agent", "plan_executor.py")
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


if __name__ == "__main__":
    unittest.main()
