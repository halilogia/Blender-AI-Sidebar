"""ProposePlanTool: Semantic meta-tool for structured multi-step plan proposal.

Enables LLM to propose a declarative execution plan for complex Blender tasks.
The plan is strictly validated against registered tools, schemas, and DAG
acyclicity without executing any scene mutations.

Zero Blender (bpy) dependencies. Pure Python standard library.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from core.types import RiskLevel, ToolResult
from agent.plan_models import Plan
from agent.plan_validator import PlanValidator
from tools.base import BaseTool
from tools.registry import ToolRegistry


class ProposePlanTool(BaseTool):
    """Semantic meta-tool for proposing and validating multi-step execution plans."""

    name = "propose_plan"
    description = (
        "Propose a structured multi-step execution plan for complex Blender tasks. "
        "The plan defines declarative steps, tools, arguments, and dependencies, "
        "and is strictly validated against available tools and DAG acyclicity before review."
    )
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {
            "plan_id": {
                "type": "string",
                "description": "Optional unique plan identifier. Auto-generated if omitted.",
            },
            "title": {
                "type": "string",
                "description": "Concise human-readable title for the execution plan.",
            },
            "description": {
                "type": "string",
                "description": "Detailed explanation of the overall goal and strategy of the plan.",
            },
            "steps": {
                "type": "array",
                "description": "List of declarative steps to execute in order of dependencies.",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {
                            "type": "string",
                            "description": "Unique identifier for this step within the plan.",
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "The exact name of the tool to execute.",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Keyword arguments conforming to the tool's input schema.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Human-readable description of this step.",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of step_ids that must complete before this step can run.",
                        },
                        "expected_result": {
                            "type": "string",
                            "description": "Optional description of the expected state or outcome after step execution.",
                        },
                    },
                    "required": ["step_id", "tool_name"],
                    "additionalProperties": False,
                },
            },
            "overall_risk": {
                "type": "string",
                "description": "Ignored: Authority is strictly derived from ToolRegistry.",
            },
            "raw_plan": {
                "type": "string",
                "description": "Optional raw JSON string representing the plan payload.",
            },
        },
        "required": ["title", "steps"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        validator: Optional[PlanValidator] = None,
    ) -> None:
        self.registry = registry
        self._validator = validator
        if self._validator is None and self.registry is not None:
            self._validator = PlanValidator(self.registry)

    def set_registry(self, registry: ToolRegistry) -> None:
        """Configure or update the ToolRegistry used for plan validation."""
        self.registry = registry
        self._validator = PlanValidator(registry)

    def _get_validator(self, adapter: Any) -> Optional[PlanValidator]:
        """Resolve PlanValidator from instance or adapter."""
        if self._validator is not None:
            return self._validator
        if self.registry is not None:
            self._validator = PlanValidator(self.registry)
            return self._validator
        if adapter is not None and hasattr(adapter, "registry") and isinstance(adapter.registry, ToolRegistry):
            self.registry = adapter.registry
            self._validator = PlanValidator(self.registry)
            return self._validator
        return None

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Parse, construct, and strictly validate a proposed execution plan.

        Zero Blender scene mutations. Zero bpy usage.
        """
        # 1. Extract payload (support raw_plan string or direct kwargs)
        data: Dict[str, Any]
        if "raw_plan" in kwargs:
            raw = kwargs["raw_plan"]
            if isinstance(raw, str):
                try:
                    data = json.loads(raw)
                except Exception as exc:
                    return ToolResult.fail(
                        tool=self.name,
                        error_type="INVALID_JSON",
                        message=f"Malformed plan JSON in 'raw_plan': {str(exc)}",
                        details={"error": str(exc)},
                    )
            elif isinstance(raw, dict):
                data = raw
            else:
                return ToolResult.fail(
                    tool=self.name,
                    error_type="INVALID_JSON",
                    message=f"Expected string or dict for 'raw_plan', got {type(raw).__name__}.",
                )
        else:
            data = dict(kwargs)

        if not isinstance(data, dict):
            return ToolResult.fail(
                tool=self.name,
                error_type="INVALID_PAYLOAD",
                message=f"Plan payload must be a dict, got {type(data).__name__}.",
            )

        # Handle string-encoded 'steps' or step arguments if present
        if isinstance(data.get("steps"), str):
            try:
                data["steps"] = json.loads(data["steps"])
            except Exception as exc:
                return ToolResult.fail(
                    tool=self.name,
                    error_type="INVALID_JSON",
                    message=f"Malformed JSON in 'steps': {str(exc)}",
                    details={"error": str(exc)},
                )

        if isinstance(data.get("steps"), (list, tuple)):
            for idx, s in enumerate(data["steps"]):
                if isinstance(s, dict) and isinstance(s.get("arguments"), str):
                    try:
                        s["arguments"] = json.loads(s["arguments"])
                    except Exception as exc:
                        return ToolResult.fail(
                            tool=self.name,
                            error_type="INVALID_JSON",
                            message=f"Malformed JSON in step index {idx} arguments: {str(exc)}",
                            details={"step_index": idx, "error": str(exc)},
                        )

        # 2. Check required top-level fields
        title = data.get("title")
        if not title or not isinstance(title, str) or not title.strip():
            return ToolResult.fail(
                tool=self.name,
                error_type="MISSING_REQUIRED_FIELD",
                message="Plan is missing required non-empty string field 'title'.",
                details={"missing_field": "title"},
            )

        if "steps" not in data or data["steps"] is None:
            return ToolResult.fail(
                tool=self.name,
                error_type="MISSING_REQUIRED_FIELD",
                message="Plan is missing required field 'steps'.",
                details={"missing_field": "steps"},
            )

        if not isinstance(data["steps"], (list, tuple)) or len(data["steps"]) == 0:
            return ToolResult.fail(
                tool=self.name,
                error_type="EMPTY_PLAN",
                message="Plan 'steps' must be a non-empty list of steps.",
            )

        # 3. Construct candidate Plan model via Plan.from_dict()
        try:
            candidate_plan = Plan.from_dict(data)
        except Exception as exc:
            return ToolResult.fail(
                tool=self.name,
                error_type="PLAN_PARSE_ERROR",
                message=f"Failed to construct Plan model: {str(exc)}",
                details={"exception": type(exc).__name__, "error": str(exc)},
            )

        # 4. Resolve validator
        validator = self._get_validator(adapter)
        if validator is None:
            return ToolResult.fail(
                tool=self.name,
                error_type="VALIDATOR_UNAVAILABLE",
                message="ToolRegistry or PlanValidator is not configured for ProposePlanTool.",
            )

        # 5. Strictly validate candidate plan (ToolRegistry, schemas, acyclicity, derived risk)
        validation_result = validator.validate(candidate_plan)
        if not validation_result.valid:
            return ToolResult.fail(
                tool=self.name,
                error_type="PLAN_VALIDATION_FAILED",
                message=f"Plan validation failed: {validation_result.error_message}",
                details={"errors": list(validation_result.errors)},
            )

        # 6. Return successful validated output (plan not executed)
        validated_plan = validation_result.plan
        return ToolResult.ok(
            tool=self.name,
            data={
                "status": "VALIDATED",
                "plan": validated_plan.to_dict(),
                "topological_order": list(validation_result.topological_order),
                "overall_risk": validated_plan.overall_risk.value,
                "steps_count": len(validated_plan.steps),
                "summary": (
                    f"Plan '{validated_plan.title}' validated successfully with "
                    f"{len(validated_plan.steps)} steps. Derived overall risk: {validated_plan.overall_risk.value}."
                ),
            },
        )
