"""Unit tests for M4.2 Plan domain data models.

Tests:
1. Declarative PlanStep structure (pure intent, no runtime status)
2. Plan container structure and validation
3. Immutability / frozen behavior of PlanStep and Plan
4. Serialization and deserialization round-trips (to_dict / from_dict)
5. Runtime outcome models (PlanStepExecutionResult, PlanExecutionSummary)
6. max_risk_level resolution and ordering
"""

from dataclasses import FrozenInstanceError
import unittest

from core.types import RiskLevel, ToolResult
from agent.plan_models import (
    Plan,
    PlanStep,
    PlanStepStatus,
    PlanStatus,
    PlanStepExecutionResult,
    PlanExecutionSummary,
    max_risk_level,
)


class TestPlanModels(unittest.TestCase):
    """Test suite for declarative plan data models."""

    def test_plan_step_declarative_creation(self):
        """PlanStep contains declarative intent and no runtime execution state."""
        step = PlanStep(
            step_id="step_1",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "size": 2.0},
            description="Create base cube",
            depends_on=("step_0",),
            expected_result="A cube named Cube at origin",
        )

        self.assertEqual(step.step_id, "step_1")
        self.assertEqual(step.tool_name, "create_primitive")
        self.assertEqual(step.arguments, {"primitive_type": "CUBE", "size": 2.0})
        self.assertEqual(step.description, "Create base cube")
        self.assertEqual(step.depends_on, ("step_0",))
        self.assertEqual(step.expected_result, "A cube named Cube at origin")

        # Must not have runtime status attribute
        self.assertFalse(hasattr(step, "status"))

    def test_plan_step_immutability(self):
        """PlanStep instances must be frozen and immutable."""
        step = PlanStep(
            step_id="step_1",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE"},
            description="Test step",
        )

        with self.assertRaises(FrozenInstanceError):
            step.description = "Mutated description"  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            step.tool_name = "delete_object"  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            step.arguments = {}  # type: ignore

    def test_plan_step_arguments_direct_mutation_rejected(self):
        """Direct item assignment and deletion on step.arguments must be rejected."""
        step = PlanStep(
            step_id="step_1",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "size": 2.0},
        )

        with self.assertRaises(TypeError):
            step.arguments["new_key"] = "forbidden"

        with self.assertRaises(TypeError):
            step.arguments["primitive_type"] = "SPHERE"

        with self.assertRaises(TypeError):
            del step.arguments["size"]

    def test_plan_step_nested_dict_mutation_rejected(self):
        """Mutation of nested dictionaries inside step.arguments must be rejected."""
        step = PlanStep(
            step_id="step_1",
            tool_name="create_primitive",
            arguments={
                "primitive_type": "CUBE",
                "nested_config": {"sub_key": "original_val", "deep": {"leaf": 42}},
            },
        )

        with self.assertRaises(TypeError):
            step.arguments["nested_config"]["sub_key"] = "mutated"

        with self.assertRaises(TypeError):
            step.arguments["nested_config"]["deep"]["leaf"] = 999

        with self.assertRaises(TypeError):
            del step.arguments["nested_config"]["sub_key"]

    def test_plan_step_nested_list_mutation_rejected(self):
        """Mutation of nested lists/arrays inside step.arguments must be rejected."""
        step = PlanStep(
            step_id="step_1",
            tool_name="create_primitive",
            arguments={
                "primitive_type": "CUBE",
                "location": [1.0, 2.0, 3.0],
                "matrix": [[1, 0], [0, 1]],
            },
        )

        # Top-level array item assignment must raise TypeError (frozen as tuple)
        with self.assertRaises(TypeError):
            step.arguments["location"][0] = 99.0

        # Nested 2D array item assignment must raise TypeError
        with self.assertRaises(TypeError):
            step.arguments["matrix"][0][0] = 99

    def test_plan_step_external_mutation_isolation(self):
        """Mutating the original dictionary or list passed into arguments must not mutate PlanStep."""
        raw_location = [1.0, 2.0, 3.0]
        raw_nested = {"color": "RED", "tags": ["tag1", "tag2"]}
        raw_args = {
            "primitive_type": "CUBE",
            "location": raw_location,
            "config": raw_nested,
        }

        step = PlanStep(step_id="s1", tool_name="create_primitive", arguments=raw_args)

        # Mutate external inputs after creation
        raw_args["primitive_type"] = "SPHERE"
        raw_args["new_arg"] = "tampered"
        raw_location.append(4.0)
        raw_location[0] = -99.0
        raw_nested["color"] = "BLUE"
        raw_nested["tags"].append("evil_tag")

        # Verify PlanStep remains completely untouched
        self.assertEqual(step.arguments["primitive_type"], "CUBE")
        self.assertNotIn("new_arg", step.arguments)
        self.assertEqual(step.arguments["location"], (1.0, 2.0, 3.0))
        self.assertEqual(step.arguments["config"]["color"], "RED")
        self.assertEqual(step.arguments["config"]["tags"], ("tag1", "tag2"))

    def test_plan_step_to_dict_returns_standard_json_types(self):
        """to_dict() must return normal Python dict and list types that serialize cleanly to JSON."""
        import json

        step = PlanStep(
            step_id="s1",
            tool_name="create_primitive",
            arguments={
                "primitive_type": "CUBE",
                "location": [1.0, 2.0, 3.0],
                "config": {"sub_key": "val", "items": [10, 20]},
            },
        )

        d = step.to_dict()
        # Verify root arguments is a standard mutable dict
        self.assertIs(type(d["arguments"]), dict)
        # Verify nested location is a standard mutable list
        self.assertIs(type(d["arguments"]["location"]), list)
        self.assertEqual(d["arguments"]["location"], [1.0, 2.0, 3.0])
        # Verify nested config is a standard mutable dict
        self.assertIs(type(d["arguments"]["config"]), dict)
        self.assertIs(type(d["arguments"]["config"]["items"]), list)

        # JSON serialization must succeed without custom encoders
        json_output = json.dumps(d)
        self.assertIsInstance(json_output, str)
        parsed = json.loads(json_output)
        self.assertEqual(parsed["arguments"]["location"], [1.0, 2.0, 3.0])

    def test_plan_step_validation_on_init(self):
        """PlanStep validates required non-empty string fields and types."""
        # Empty step_id
        with self.assertRaises(ValueError):
            PlanStep(step_id="", tool_name="create_primitive")

        # Whitespace step_id
        with self.assertRaises(ValueError):
            PlanStep(step_id="   ", tool_name="create_primitive")

        # Empty tool_name
        with self.assertRaises(ValueError):
            PlanStep(step_id="step_1", tool_name="")

        # Non-dict arguments
        with self.assertRaises(TypeError):
            PlanStep(step_id="step_1", tool_name="create_primitive", arguments="not_a_dict")  # type: ignore

        # Non-string description
        with self.assertRaises(TypeError):
            PlanStep(step_id="step_1", tool_name="create_primitive", description=123)  # type: ignore

        # Invalid dependency elements
        with self.assertRaises(ValueError):
            PlanStep(step_id="step_1", tool_name="create_primitive", depends_on=("",))

    def test_plan_step_serialization_roundtrip(self):
        """PlanStep to_dict and from_dict roundtrip preserves exact values."""
        step = PlanStep(
            step_id="step_1",
            tool_name="create_primitive",
            arguments={"primitive_type": "SPHERE", "size": 1.5},
            description="Create sphere",
            depends_on=("step_0",),
            expected_result="Sphere created",
        )
        d = step.to_dict()
        self.assertEqual(d["step_id"], "step_1")
        self.assertEqual(d["tool_name"], "create_primitive")
        self.assertEqual(d["arguments"], {"primitive_type": "SPHERE", "size": 1.5})
        self.assertEqual(d["depends_on"], ["step_0"])
        self.assertEqual(d["expected_result"], "Sphere created")

        reconstructed = PlanStep.from_dict(d)
        self.assertEqual(reconstructed, step)

    def test_plan_creation_and_immutability(self):
        """Plan is frozen and immutable."""
        s1 = PlanStep(step_id="s1", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
        s2 = PlanStep(step_id="s2", tool_name="transform_object", arguments={"name": "Cube"}, depends_on=("s1",))

        plan = Plan(
            plan_id="plan_abc",
            title="Create and transform",
            description="Two-step plan",
            steps=(s1, s2),
            overall_risk=RiskLevel.LOW,
        )

        self.assertEqual(plan.plan_id, "plan_abc")
        self.assertEqual(plan.title, "Create and transform")
        self.assertEqual(plan.description, "Two-step plan")
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0], s1)
        self.assertEqual(plan[1], s2)
        self.assertEqual(plan.get_step("s1"), s1)
        self.assertIsNone(plan.get_step("non_existent"))

        with self.assertRaises(FrozenInstanceError):
            plan.title = "Mutated title"  # type: ignore

        with self.assertRaises(FrozenInstanceError):
            plan.steps = ()  # type: ignore

    def test_plan_validation_on_init(self):
        """Plan validates required fields."""
        with self.assertRaises(ValueError):
            Plan(plan_id="", title="Valid", description="desc")

        with self.assertRaises(ValueError):
            Plan(plan_id="plan_1", title="", description="desc")

        with self.assertRaises(TypeError):
            Plan(plan_id="plan_1", title="Valid", description=123)  # type: ignore

        with self.assertRaises(TypeError):
            Plan(plan_id="plan_1", title="Valid", description="desc", steps="not_steps")  # type: ignore

        with self.assertRaises(TypeError):
            Plan(plan_id="plan_1", title="Valid", description="desc", steps=("not_a_step",))  # type: ignore

    def test_plan_serialization_roundtrip(self):
        """Plan to_dict and from_dict roundtrip preserves all attributes."""
        s1 = PlanStep(step_id="s1", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
        plan = Plan(
            plan_id="plan_xyz",
            title="My Plan",
            description="Description",
            steps=(s1,),
            overall_risk=RiskLevel.MEDIUM,
        )

        d = plan.to_dict()
        self.assertEqual(d["plan_id"], "plan_xyz")
        self.assertEqual(d["title"], "My Plan")
        self.assertEqual(d["overall_risk"], "MEDIUM")
        self.assertEqual(len(d["steps"]), 1)

        reconstructed = Plan.from_dict(d)
        self.assertEqual(reconstructed.plan_id, plan.plan_id)
        self.assertEqual(reconstructed.title, plan.title)
        self.assertEqual(reconstructed.overall_risk, plan.overall_risk)
        self.assertEqual(len(reconstructed.steps), 1)
        self.assertEqual(reconstructed.steps[0], s1)

    def test_runtime_execution_models(self):
        """PlanStepExecutionResult and PlanExecutionSummary represent runtime outcomes."""
        res = ToolResult.ok(tool="create_primitive", data={"created": "Cube"})
        step_res = PlanStepExecutionResult(
            step_id="s1",
            tool_name="create_primitive",
            status=PlanStepStatus.COMPLETED,
            tool_result=res,
            duration_seconds=0.045,
        )

        self.assertEqual(step_res.step_id, "s1")
        self.assertEqual(step_res.status, PlanStepStatus.COMPLETED)
        self.assertIsNotNone(step_res.tool_result)
        self.assertTrue(step_res.tool_result.success)

        d = step_res.to_dict()
        self.assertEqual(d["status"], "COMPLETED")
        self.assertEqual(d["tool_name"], "create_primitive")

        reconstructed_step_res = PlanStepExecutionResult.from_dict(d)
        self.assertEqual(reconstructed_step_res.step_id, "s1")
        self.assertEqual(reconstructed_step_res.status, PlanStepStatus.COMPLETED)

        summary = PlanExecutionSummary(
            plan_id="plan_1",
            status=PlanStatus.COMPLETED,
            steps_total=1,
            steps_completed=1,
            step_results=(step_res,),
        )

        summary_dict = summary.to_dict()
        self.assertEqual(summary_dict["status"], "COMPLETED")
        self.assertEqual(summary_dict["steps_completed"], 1)

        reconstructed_summary = PlanExecutionSummary.from_dict(summary_dict)
        self.assertEqual(reconstructed_summary.plan_id, "plan_1")
        self.assertEqual(reconstructed_summary.status, PlanStatus.COMPLETED)
        self.assertEqual(len(reconstructed_summary.step_results), 1)

    def test_max_risk_level_resolution(self):
        """max_risk_level correctly resolves the highest risk among items."""
        self.assertEqual(max_risk_level([]), RiskLevel.LOW)
        self.assertEqual(max_risk_level([RiskLevel.READ_ONLY]), RiskLevel.READ_ONLY)
        self.assertEqual(max_risk_level([RiskLevel.READ_ONLY, RiskLevel.LOW]), RiskLevel.LOW)
        self.assertEqual(max_risk_level([RiskLevel.LOW, RiskLevel.MEDIUM]), RiskLevel.MEDIUM)
        self.assertEqual(max_risk_level([RiskLevel.MEDIUM, RiskLevel.HIGH]), RiskLevel.HIGH)
        self.assertEqual(max_risk_level([RiskLevel.HIGH, RiskLevel.CRITICAL]), RiskLevel.CRITICAL)
        self.assertEqual(
            max_risk_level([RiskLevel.CRITICAL, RiskLevel.READ_ONLY, RiskLevel.LOW]),
            RiskLevel.CRITICAL,
        )
        # String representations
        self.assertEqual(max_risk_level(["LOW", "HIGH"]), RiskLevel.HIGH)


if __name__ == "__main__":
    unittest.main()
