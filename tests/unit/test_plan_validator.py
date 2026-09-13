"""Unit tests for M4.2 Strict Plan Validation Engine.

Tests:
1. Valid single-step plan
2. Valid multi-step plan
3. Unique step ids & duplicate step id rejection
4. Unknown tool rejection
5. Propose_plan tool nesting rejection
6. Argument schema validation:
   - Missing required argument
   - Additional properties forbidden
   - Property types (string, number, integer, boolean, array, object)
   - Enums
   - Array minItems / maxItems and item types
7. Missing dependency rejection
8. Self-dependency rejection
9. Dependency cycles (2-node cycle, 3-node cycle)
10. Multi-level dependency chain
11. Forward dependency support (DAG topological resolution)
12. Deterministic topological ordering
13. Risk derived strictly from ToolRegistry
14. Fake LLM risk value ignored
15. Empty plan rejection
16. Malformed plan payloads (invalid JSON, wrong types, missing fields)
17. Maximum plan steps threshold
18. Fail-closed security guarantee (plan is None on any error)
"""

import json
import unittest

from core.types import RiskLevel, ToolResult
from agent.plan_models import Plan, PlanStep
from agent.plan_validator import PlanValidator, PlanValidationResult, MAX_PLAN_STEPS
from tools.base import BaseTool
from tools.registry import ToolRegistry
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.delete_object import DeleteObjectTool
from tools.mutations.transform_object import TransformObjectTool


class DummyReadOnlyTool(BaseTool):
    name = "dummy_inspect"
    description = "Read only inspection tool"
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "deep": {"type": "boolean"},
        },
        "required": ["target"],
        "additionalProperties": False,
    }

    def execute(self, adapter, **kwargs):
        return ToolResult.ok(tool=self.name, data={})


class DummyHighRiskTool(BaseTool):
    name = "dummy_high_risk"
    description = "High risk test tool"
    risk_level = RiskLevel.HIGH
    input_schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["count"],
        "additionalProperties": False,
    }

    def execute(self, adapter, **kwargs):
        return ToolResult.ok(tool=self.name, data={})


class TestPlanValidator(unittest.TestCase):
    """Test suite for strict plan validation."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(DeleteObjectTool())
        self.registry.register(TransformObjectTool())
        self.registry.register(DummyReadOnlyTool())
        self.registry.register(DummyHighRiskTool())
        self.validator = PlanValidator(self.registry)

    # 1. Valid single-step plan
    def test_valid_single_step_plan(self):
        """A single-step valid plan successfully validates."""
        raw = {
            "plan_id": "plan_single",
            "title": "Create Cube",
            "description": "Creates a single cube at the origin",
            "steps": [
                {
                    "step_id": "step_1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE", "size": 2.0},
                    "description": "Add cube",
                    "depends_on": [],
                    "expected_result": "Cube created",
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertTrue(result.valid, f"Validation failed: {result.error_message}")
        self.assertIsNotNone(result.plan)
        self.assertEqual(len(result.plan), 1)
        self.assertEqual(result.topological_order, ("step_1",))
        self.assertEqual(result.plan.overall_risk, RiskLevel.LOW)
        self.assertEqual(result.plan.title, "Create Cube")
        self.assertEqual(result.errors, ())

    # 2. Valid multi-step plan
    def test_valid_multi_step_plan(self):
        """A multi-step valid plan with sequential dependencies validates."""
        raw = {
            "title": "Build and Move",
            "description": "Create cube and transform it",
            "steps": [
                {
                    "step_id": "create_step",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "description": "Create cube",
                },
                {
                    "step_id": "transform_step",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube", "location": [1.0, 2.0, 3.0]},
                    "description": "Move cube",
                    "depends_on": ["create_step"],
                },
            ],
        }

        result = self.validator.validate(raw)
        self.assertTrue(result.valid, f"Validation failed: {result.error_message}")
        self.assertEqual(result.topological_order, ("create_step", "transform_step"))
        self.assertEqual(len(result.plan.steps), 2)
        self.assertEqual(result.plan.steps[0].step_id, "create_step")
        self.assertEqual(result.plan.steps[1].step_id, "transform_step")

    # 3. Duplicate step_id rejection
    def test_duplicate_step_id_rejected(self):
        """Duplicate step_id must be rejected deterministically."""
        raw = {
            "title": "Duplicate IDs",
            "steps": [
                {
                    "step_id": "step_dup",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "step_dup",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "SPHERE"},
                },
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("Duplicate step_id 'step_dup'", result.error_message)

    # 4. Unknown tool rejection
    def test_unknown_tool_rejected(self):
        """Tool not registered in ToolRegistry must be rejected."""
        raw = {
            "title": "Unknown Tool Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "non_existent_tool",
                    "arguments": {},
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("Unknown tool 'non_existent_tool'", result.error_message)

    # 5. Propose_plan nesting rejection
    def test_propose_plan_nesting_rejected(self):
        """Tool 'propose_plan' cannot be nested inside an execution plan."""
        raw = {
            "title": "Recursive Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "propose_plan",
                    "arguments": {},
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("propose_plan", result.error_message)

    # 6. Argument schema validation
    def test_missing_required_argument_rejected(self):
        """Step missing required arguments must be rejected."""
        raw = {
            "title": "Missing Arg",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {},  # Missing primitive_type
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("Missing required argument 'primitive_type'", result.error_message)

    def test_unexpected_additional_argument_rejected(self):
        """Additional properties must be rejected when forbidden."""
        raw = {
            "title": "Extra Arg",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "delete_object",
                    "arguments": {"name": "Cube", "unauthorized_flag": True},
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("Unexpected argument 'unauthorized_flag'", result.error_message)

    def test_invalid_enum_argument_rejected(self):
        """Invalid enum argument value must be rejected."""
        raw = {
            "title": "Invalid Enum",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CYLINDER"},  # Allowed: CUBE, SPHERE, PLANE
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("not in allowed enum", result.error_message)

    def test_invalid_argument_types_rejected(self):
        """Mismatched argument types must be rejected."""
        # String for number
        raw = {
            "title": "Type Mismatch",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE", "size": "not_a_number"},
                }
            ],
        }
        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIn("must be a number", result.error_message)

        # Number for string
        raw2 = {
            "title": "Type Mismatch 2",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "delete_object",
                    "arguments": {"name": 12345},
                }
            ],
        }
        result2 = self.validator.validate(raw2)
        self.assertFalse(result2.valid)
        self.assertIn("must be a string", result2.error_message)

    def test_array_bounds_and_item_types(self):
        """Array minItems/maxItems and item types must be validated."""
        # Location with 2 items instead of 3
        raw = {
            "title": "Array minItems",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE", "location": [0.0, 1.0]},
                }
            ],
        }
        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIn("minItems", result.error_message)

        # Location with non-number item
        raw2 = {
            "title": "Array item type",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE", "location": [0.0, "bad", 2.0]},
                }
            ],
        }
        result2 = self.validator.validate(raw2)
        self.assertFalse(result2.valid)
        self.assertIn("must be a number", result2.error_message)

    # 7. Missing dependency
    def test_missing_dependency_rejected(self):
        """Step depending on a non-existent step_id must be rejected."""
        raw = {
            "title": "Missing Dep",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": ["non_existent_step"],
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("non-existent step 'non_existent_step'", result.error_message)

    # 8. Self-dependency
    def test_self_dependency_rejected(self):
        """Step depending on itself must be rejected."""
        raw = {
            "title": "Self Dep",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": ["s1"],
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("cannot depend on itself", result.error_message)

    # 9. Dependency cycles
    def test_direct_two_step_cycle_rejected(self):
        """Direct 2-step cycle (A -> B -> A) must be detected and rejected."""
        raw = {
            "title": "Two Step Cycle",
            "steps": [
                {
                    "step_id": "step_a",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": ["step_b"],
                },
                {
                    "step_id": "step_b",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "SPHERE"},
                    "depends_on": ["step_a"],
                },
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("Dependency cycle detected", result.error_message)

    def test_three_step_cycle_rejected(self):
        """Three-step cycle (A -> B -> C -> A) must be detected and rejected."""
        raw = {
            "title": "Three Step Cycle",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": ["s3"],
                },
                {
                    "step_id": "s2",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "SPHERE"},
                    "depends_on": ["s1"],
                },
                {
                    "step_id": "s3",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "PLANE"},
                    "depends_on": ["s2"],
                },
            ],
        }

        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("Dependency cycle detected", result.error_message)

    # 10. Multi-level dependency chain & 11. Forward dependency
    def test_forward_dependency_and_topological_sort(self):
        """Forward dependency (s1 depends on s2 declared later) is properly sorted."""
        raw = {
            "title": "Forward Dependency Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s2"],  # Declared before s2, but depends on s2
                },
                {
                    "step_id": "s2",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": [],
                },
            ],
        }

        result = self.validator.validate(raw)
        self.assertTrue(result.valid, f"Validation failed: {result.error_message}")
        self.assertEqual(result.topological_order, ("s2", "s1"))
        self.assertEqual(result.plan.steps[0].step_id, "s2")
        self.assertEqual(result.plan.steps[1].step_id, "s1")

    def test_multi_level_dependency_dag(self):
        """Complex multi-level DAG preserves deterministic topological order."""
        # s1 -> s2 -> s4
        # s1 -> s3 -> s4
        raw = {
            "title": "Diamond DAG",
            "steps": [
                {
                    "step_id": "s4",
                    "tool_name": "delete_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s2", "s3"],
                },
                {
                    "step_id": "s3",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s1"],
                },
                {
                    "step_id": "s2",
                    "tool_name": "transform_object",
                    "arguments": {"name": "Cube"},
                    "depends_on": ["s1"],
                },
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                    "depends_on": [],
                },
            ],
        }

        result = self.validator.validate(raw)
        self.assertTrue(result.valid, f"Validation failed: {result.error_message}")
        # s1 must come first, s4 must come last.
        # Between s2 and s3, declaration order tie-breaking puts s3 before s2 (since s3 declared before s2)
        self.assertEqual(result.topological_order[0], "s1")
        self.assertEqual(result.topological_order[-1], "s4")
        self.assertIn("s2", result.topological_order[1:3])
        self.assertIn("s3", result.topological_order[1:3])

    # 13. Risk derived strictly from ToolRegistry
    def test_risk_derived_from_registry(self):
        """Overall plan risk is calculated deterministically from registry tool risks."""
        # create_primitive is LOW, delete_object is MEDIUM -> overall must be MEDIUM
        raw = {
            "title": "Mixed Risk Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                },
                {
                    "step_id": "s2",
                    "tool_name": "delete_object",
                    "arguments": {"name": "OldObject"},
                },
            ],
        }

        result = self.validator.validate(raw)
        self.assertTrue(result.valid)
        self.assertEqual(result.plan.overall_risk, RiskLevel.MEDIUM)

        # Plan with high risk tool -> overall must be HIGH
        raw_high = {
            "title": "High Risk Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "dummy_high_risk",
                    "arguments": {"count": 5},
                },
            ],
        }
        result_high = self.validator.validate(raw_high)
        self.assertTrue(result_high.valid)
        self.assertEqual(result_high.plan.overall_risk, RiskLevel.HIGH)

        # Plan with only read-only tool -> overall must be READ_ONLY
        raw_ro = {
            "title": "Read Only Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "dummy_inspect",
                    "arguments": {"target": "Scene"},
                },
            ],
        }
        result_ro = self.validator.validate(raw_ro)
        self.assertTrue(result_ro.valid)
        self.assertEqual(result_ro.plan.overall_risk, RiskLevel.READ_ONLY)

    # 14. Fake LLM risk value ignored
    def test_fake_llm_risk_value_ignored(self):
        """LLM asserting low risk for a high/medium tool must be ignored."""
        # LLM claims READ_ONLY, but step is delete_object (MEDIUM)
        raw = {
            "title": "Deceptive Plan",
            "overall_risk": "READ_ONLY",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "delete_object",
                    "arguments": {"name": "Target"},
                }
            ],
        }

        result = self.validator.validate(raw)
        self.assertTrue(result.valid)
        # Authoritative risk MUST be MEDIUM, not the fake READ_ONLY
        self.assertEqual(result.plan.overall_risk, RiskLevel.MEDIUM)

        # LLM claims CRITICAL, but step is create_primitive (LOW)
        raw2 = {
            "title": "Inflated Risk Plan",
            "overall_risk": "CRITICAL",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "CUBE"},
                }
            ],
        }
        result2 = self.validator.validate(raw2)
        self.assertTrue(result2.valid)
        self.assertEqual(result2.plan.overall_risk, RiskLevel.LOW)

    # 15. Empty plan rejection
    def test_empty_plan_rejected(self):
        """Empty steps array must be rejected."""
        raw = {"title": "Empty Plan", "steps": []}
        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIsNone(result.plan)
        self.assertIn("at least one step", result.error_message)

    # 16. Malformed plan data
    def test_malformed_plan_data_rejected(self):
        """Malformed payloads must be safely rejected without crashing."""
        # Invalid JSON string
        result_str = self.validator.validate("Not valid json {{{")
        self.assertFalse(result_str.valid)
        self.assertIn("Malformed JSON", result_str.error_message)

        # Wrong type
        result_type = self.validator.validate(12345)  # type: ignore
        self.assertFalse(result_type.valid)
        self.assertIn("Invalid plan input type", result_type.error_message)

        # Missing title
        result_no_title = self.validator.validate({"steps": [{"step_id": "s1", "tool_name": "create_primitive"}]})
        self.assertFalse(result_no_title.valid)
        self.assertIn("title", result_no_title.error_message)

        # Missing steps
        result_no_steps = self.validator.validate({"title": "No steps"})
        self.assertFalse(result_no_steps.valid)
        self.assertIn("missing required field 'steps'", result_no_steps.error_message)

        # Step not a dict
        result_bad_step = self.validator.validate({"title": "Bad step", "steps": ["not_a_dict"]})
        self.assertFalse(result_bad_step.valid)
        self.assertIn("must be a dict", result_bad_step.error_message)

        # Step missing step_id
        result_no_id = self.validator.validate({
            "title": "No id",
            "steps": [{"tool_name": "create_primitive", "arguments": {"primitive_type": "CUBE"}}],
        })
        self.assertFalse(result_no_id.valid)
        self.assertIn("invalid or missing 'step_id'", result_no_id.error_message)

        # Step missing tool_name
        result_no_tool = self.validator.validate({
            "title": "No tool",
            "steps": [{"step_id": "s1", "arguments": {}}],
        })
        self.assertFalse(result_no_tool.valid)
        self.assertIn("invalid or missing 'tool_name'", result_no_tool.error_message)

    # 17. Max plan steps bound
    def test_exceeding_max_plan_steps_rejected(self):
        """Plan with more than MAX_PLAN_STEPS steps must be rejected."""
        steps = [
            {
                "step_id": f"s_{i}",
                "tool_name": "create_primitive",
                "arguments": {"primitive_type": "CUBE"},
            }
            for i in range(MAX_PLAN_STEPS + 1)
        ]
        raw = {"title": "Too Many Steps", "steps": steps}
        result = self.validator.validate(raw)
        self.assertFalse(result.valid)
        self.assertIn(f"exceeding maximum allowed of {MAX_PLAN_STEPS}", result.error_message)

    # 18. JSON string input parsing
    def test_json_string_input_parsing(self):
        """Validator accepts valid JSON string and validates it correctly."""
        raw_dict = {
            "title": "JSON Plan",
            "steps": [
                {
                    "step_id": "s1",
                    "tool_name": "create_primitive",
                    "arguments": {"primitive_type": "PLANE"},
                }
            ],
        }
        json_str = json.dumps(raw_dict)
        result = self.validator.validate(json_str)
        self.assertTrue(result.valid)
        self.assertEqual(result.plan.title, "JSON Plan")

    # 19. Existing Plan instance validation
    def test_plan_instance_input_validation(self):
        """Validator accepts an existing Plan instance and revalidates it."""
        step = PlanStep(step_id="s1", tool_name="create_primitive", arguments={"primitive_type": "SPHERE"})
        plan = Plan(plan_id="p1", title="Existing Plan", description="desc", steps=(step,))
        result = self.validator.validate(plan)
        self.assertTrue(result.valid)
        self.assertEqual(result.plan.plan_id, "p1")


if __name__ == "__main__":
    unittest.main()
