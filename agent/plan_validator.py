"""Strict deterministic validation engine for execution plans.

Zero Blender (bpy) dependencies. Pure Python standard library.
Enforces:
1. Tool existence in ToolRegistry.
2. Argument schema compliance (required fields, additional properties, types, enums).
3. Unique, non-empty step identifiers.
4. Dependency validation (no self-dependencies, no missing dependencies).
5. DAG cycle detection and deterministic topological ordering via Kahn's algorithm.
6. Derived risk calculation from ToolRegistry (LLM assertions ignored).
7. Strict bounds on plan length and fail-closed validation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid

from core.types import RiskLevel
from agent.plan_models import Plan, PlanStep, max_risk_level
from tools.base import BaseTool
from tools.registry import ToolRegistry

MAX_PLAN_STEPS: int = 20


@dataclass(frozen=True)
class PlanValidationResult:
    """Outcome of validating a plan against the ToolRegistry and execution DAG."""

    valid: bool
    plan: Optional[Plan] = None
    topological_order: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()

    @property
    def error_message(self) -> str:
        """Concatenated human-readable description of all validation errors."""
        return "; ".join(self.errors) if self.errors else ""


def validate_tool_arguments(tool: BaseTool, arguments: Any) -> List[str]:
    """Validate tool call arguments against the tool's input_schema.

    Returns a list of error strings, or empty list if valid.
    """
    errors: List[str] = []
    if not isinstance(arguments, (dict, MappingProxyType)):
        return [f"Arguments must be a dict, got {type(arguments).__name__}."]

    schema = getattr(tool, "input_schema", {})
    if not isinstance(schema, dict):
        return []

    # 1. Required properties check
    required_fields = schema.get("required", [])
    for field in required_fields:
        if field not in arguments:
            errors.append(f"Missing required argument '{field}'.")

    # 2. Additional properties check
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        for arg_name in arguments:
            if arg_name not in allowed:
                errors.append(f"Unexpected argument '{arg_name}'. Allowed: {sorted(allowed)}.")

    # 3. Property types, enums, and bounds check
    for prop_name, prop_val in arguments.items():
        if prop_name in properties and isinstance(properties[prop_name], dict):
            p_spec = properties[prop_name]
            p_type = p_spec.get("type")

            # Check enum membership
            if "enum" in p_spec and isinstance(p_spec["enum"], (list, tuple)):
                if prop_val not in p_spec["enum"]:
                    errors.append(
                        f"Argument '{prop_name}' value {prop_val!r} is not in allowed enum {p_spec['enum']}."
                    )

            # Check type constraints
            if p_type == "string" and not isinstance(prop_val, str):
                errors.append(f"Argument '{prop_name}' must be a string, got {type(prop_val).__name__}.")
            elif p_type == "number" and (not isinstance(prop_val, (int, float)) or isinstance(prop_val, bool)):
                errors.append(f"Argument '{prop_name}' must be a number, got {type(prop_val).__name__}.")
            elif p_type == "integer" and (not isinstance(prop_val, int) or isinstance(prop_val, bool)):
                errors.append(f"Argument '{prop_name}' must be an integer, got {type(prop_val).__name__}.")
            elif p_type == "boolean" and not isinstance(prop_val, bool):
                errors.append(f"Argument '{prop_name}' must be a boolean, got {type(prop_val).__name__}.")
            elif p_type == "array":
                if not isinstance(prop_val, (list, tuple)):
                    errors.append(f"Argument '{prop_name}' must be a list or tuple, got {type(prop_val).__name__}.")
                else:
                    if "minItems" in p_spec and len(prop_val) < p_spec["minItems"]:
                        errors.append(f"Argument '{prop_name}' length {len(prop_val)} < minItems {p_spec['minItems']}.")
                    if "maxItems" in p_spec and len(prop_val) > p_spec["maxItems"]:
                        errors.append(f"Argument '{prop_name}' length {len(prop_val)} > maxItems {p_spec['maxItems']}.")
                    items_spec = p_spec.get("items")
                    if isinstance(items_spec, dict):
                        item_type = items_spec.get("type")
                        for idx, item in enumerate(prop_val):
                            if item_type == "number" and (not isinstance(item, (int, float)) or isinstance(item, bool)):
                                errors.append(f"Argument '{prop_name}[{idx}]' must be a number, got {type(item).__name__}.")
                            elif item_type == "string" and not isinstance(item, str):
                                errors.append(f"Argument '{prop_name}[{idx}]' must be a string, got {type(item).__name__}.")
                            elif item_type == "integer" and (not isinstance(item, int) or isinstance(item, bool)):
                                errors.append(f"Argument '{prop_name}[{idx}]' must be an integer, got {type(item).__name__}.")
                            elif item_type == "boolean" and not isinstance(item, bool):
                                errors.append(f"Argument '{prop_name}[{idx}]' must be a boolean, got {type(item).__name__}.")
            elif p_type == "object" and not isinstance(prop_val, (dict, MappingProxyType)):
                errors.append(f"Argument '{prop_name}' must be an object/dict, got {type(prop_val).__name__}.")

    return errors


class PlanValidator:
    """Deterministic validator for multi-step execution plans."""

    def __init__(self, registry: ToolRegistry) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError(f"PlanValidator requires ToolRegistry, got {type(registry).__name__}.")
        self.registry = registry

    def validate(self, raw_plan: Union[Plan, Dict[str, Any], str]) -> PlanValidationResult:
        """Validate a plan against registry tools, schemas, and DAG acyclicity.

        Args:
            raw_plan: Plan instance, dictionary, or JSON string.

        Returns:
            PlanValidationResult with valid=True and topological-ordered Plan, or valid=False with errors.
        """
        errors: List[str] = []

        # 1. Parse into dictionary if JSON string or handle invalid type
        if isinstance(raw_plan, str):
            try:
                data = json.loads(raw_plan)
            except Exception as exc:
                return PlanValidationResult(valid=False, errors=(f"Malformed JSON plan: {str(exc)}",))
        elif isinstance(raw_plan, Plan):
            data = raw_plan.to_dict()
        elif isinstance(raw_plan, dict):
            data = raw_plan
        else:
            return PlanValidationResult(
                valid=False,
                errors=(f"Invalid plan input type: expected dict, Plan, or JSON string, got {type(raw_plan).__name__}.",),
            )

        if not isinstance(data, dict):
            return PlanValidationResult(valid=False, errors=("Plan payload must be a JSON object / dictionary.",))

        # 2. Validate top-level plan fields
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append("Plan 'title' must be a non-empty string.")
        else:
            title = title.strip()

        description = data.get("description", "")
        if not isinstance(description, str):
            errors.append("Plan 'description' must be a string.")
            description = ""

        plan_id = data.get("plan_id")
        if not plan_id or not isinstance(plan_id, str) or not plan_id.strip():
            plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        else:
            plan_id = plan_id.strip()

        raw_steps = data.get("steps")
        if raw_steps is None:
            errors.append("Plan is missing required field 'steps'.")
            return PlanValidationResult(valid=False, errors=tuple(errors))
        if not isinstance(raw_steps, (list, tuple)):
            errors.append(f"Plan 'steps' must be a list, got {type(raw_steps).__name__}.")
            return PlanValidationResult(valid=False, errors=tuple(errors))

        if len(raw_steps) == 0:
            errors.append("Plan must contain at least one step.")
            return PlanValidationResult(valid=False, errors=tuple(errors))

        if len(raw_steps) > MAX_PLAN_STEPS:
            errors.append(f"Plan contains {len(raw_steps)} steps, exceeding maximum allowed of {MAX_PLAN_STEPS}.")
            return PlanValidationResult(valid=False, errors=tuple(errors))

        # 3. Validate individual steps structure & uniqueness
        parsed_steps: Dict[str, Dict[str, Any]] = {}
        step_order_index: Dict[str, int] = {}

        for idx, raw_step in enumerate(raw_steps):
            if isinstance(raw_step, PlanStep):
                step_dict = raw_step.to_dict()
            elif isinstance(raw_step, dict):
                step_dict = raw_step
            else:
                errors.append(f"Step at index {idx} must be a dict or PlanStep, got {type(raw_step).__name__}.")
                continue

            step_id = step_dict.get("step_id")
            if not isinstance(step_id, str) or not step_id.strip():
                errors.append(f"Step at index {idx} has invalid or missing 'step_id'.")
                continue
            step_id = step_id.strip()

            if step_id in parsed_steps:
                errors.append(f"Duplicate step_id '{step_id}' found in plan.")
                continue

            tool_name = step_dict.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                errors.append(f"Step '{step_id}' has invalid or missing 'tool_name'.")
                continue
            tool_name = tool_name.strip()

            # Prevent nesting propose_plan inside a plan
            if tool_name == "propose_plan":
                errors.append(f"Step '{step_id}': Tool 'propose_plan' cannot be nested inside a plan.")
                continue

            args = step_dict.get("arguments")
            if args is None:
                args = {}
            elif not isinstance(args, dict):
                errors.append(f"Step '{step_id}': 'arguments' must be a dict, got {type(args).__name__}.")
                continue

            desc = step_dict.get("description", "")
            if not isinstance(desc, str):
                errors.append(f"Step '{step_id}': 'description' must be a string.")
                desc = ""

            deps = step_dict.get("depends_on", ())
            if not isinstance(deps, (list, tuple)):
                errors.append(f"Step '{step_id}': 'depends_on' must be a list or tuple.")
                deps = ()
            else:
                clean_deps = []
                for d in deps:
                    if not isinstance(d, str) or not d.strip():
                        errors.append(f"Step '{step_id}': Invalid dependency identifier {d!r}.")
                    else:
                        clean_deps.append(d.strip())
                deps = tuple(clean_deps)

            exp_result = step_dict.get("expected_result")
            if exp_result is not None and not isinstance(exp_result, str):
                errors.append(f"Step '{step_id}': 'expected_result' must be a string or None.")
                exp_result = None

            parsed_steps[step_id] = {
                "step_id": step_id,
                "tool_name": tool_name,
                "arguments": args,
                "description": desc,
                "depends_on": deps,
                "expected_result": exp_result,
            }
            step_order_index[step_id] = idx

        # If structural step errors occurred, fail immediately before registry/DAG checks
        if errors:
            return PlanValidationResult(valid=False, errors=tuple(errors))

        all_step_ids: Set[str] = set(parsed_steps.keys())

        # 4. Validate tool existence & argument schema
        step_risks: List[RiskLevel] = []
        for step_id, step_data in parsed_steps.items():
            t_name = step_data["tool_name"]
            if not self.registry.exists(t_name):
                errors.append(f"Step '{step_id}': Unknown tool '{t_name}' not found in ToolRegistry.")
                continue

            tool = self.registry.get(t_name)
            t_risk = getattr(tool, "risk_level", RiskLevel.HIGH)
            step_risks.append(t_risk)

            # Validate arguments against tool input schema
            arg_errors = validate_tool_arguments(tool, step_data["arguments"])
            for ae in arg_errors:
                errors.append(f"Step '{step_id}' ({t_name}): {ae}")

        # 5. Validate dependencies (no self-dependency, no missing targets)
        for step_id, step_data in parsed_steps.items():
            for dep_id in step_data["depends_on"]:
                if dep_id == step_id:
                    errors.append(f"Step '{step_id}' cannot depend on itself.")
                elif dep_id not in all_step_ids:
                    errors.append(f"Step '{step_id}' depends on non-existent step '{dep_id}'.")

        if errors:
            return PlanValidationResult(valid=False, errors=tuple(errors))

        # 6. DAG Cycle Detection & Deterministic Topological Sort (Kahn's Algorithm)
        in_degree: Dict[str, int] = {s: 0 for s in all_step_ids}
        adj_graph: Dict[str, List[str]] = {s: [] for s in all_step_ids}

        for step_id, step_data in parsed_steps.items():
            for dep_id in step_data["depends_on"]:
                adj_graph[dep_id].append(step_id)
                in_degree[step_id] += 1

        # Queue nodes with zero in-degree
        ready_queue: List[str] = [s for s in all_step_ids if in_degree[s] == 0]
        topological_order: List[str] = []

        while ready_queue:
            # Deterministic tie-breaking: preserve original declaration order
            ready_queue.sort(key=lambda sid: step_order_index[sid])
            current_id = ready_queue.pop(0)
            topological_order.append(current_id)

            for neighbor_id in adj_graph[current_id]:
                in_degree[neighbor_id] -= 1
                if in_degree[neighbor_id] == 0:
                    ready_queue.append(neighbor_id)

        if len(topological_order) != len(all_step_ids):
            cycle_steps = [s for s in all_step_ids if in_degree[s] > 0]
            errors.append(f"Dependency cycle detected in plan involving steps: {sorted(cycle_steps)}.")
            return PlanValidationResult(valid=False, errors=tuple(errors))

        # 7. Derive overall risk deterministically from registry tools (ignore any LLM-supplied risk)
        overall_risk = max_risk_level(step_risks)

        # 8. Construct validated immutable Plan with steps ordered by topological sort
        topological_plan_steps: List[PlanStep] = []
        for step_id in topological_order:
            s_data = parsed_steps[step_id]
            topological_plan_steps.append(
                PlanStep(
                    step_id=s_data["step_id"],
                    tool_name=s_data["tool_name"],
                    arguments=s_data["arguments"],
                    description=s_data["description"],
                    depends_on=s_data["depends_on"],
                    expected_result=s_data["expected_result"],
                )
            )

        validated_plan = Plan(
            plan_id=plan_id,
            title=title,
            description=description,
            steps=tuple(topological_plan_steps),
            overall_risk=overall_risk,
        )

        return PlanValidationResult(
            valid=True,
            plan=validated_plan,
            topological_order=tuple(topological_order),
            errors=(),
        )
