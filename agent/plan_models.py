"""Domain data models for structured multi-step execution plans.

Zero Blender (bpy) dependencies. Pure Python standard library.
Enforces strict immutability, clean separation of declarative plan intent
from runtime execution outcomes, and deterministic schema serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from core.types import RiskLevel, ToolError, ToolResult

RISK_ORDER: Dict[RiskLevel, int] = {
    RiskLevel.READ_ONLY: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


def max_risk_level(risks: Iterable[Union[RiskLevel, str]]) -> RiskLevel:
    """Determine the maximum risk level from an iterable of RiskLevels.

    Defaults to RiskLevel.LOW if risks iterable is empty.
    """
    resolved: List[RiskLevel] = []
    for r in risks:
        if isinstance(r, RiskLevel):
            resolved.append(r)
        elif isinstance(r, str):
            try:
                resolved.append(RiskLevel(r))
            except ValueError:
                resolved.append(RiskLevel.HIGH)
    if not resolved:
        return RiskLevel.LOW
    return max(resolved, key=lambda r: RISK_ORDER.get(r, 3))


class PlanStepStatus(str, Enum):
    """Execution status of an individual step within a plan."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class PlanStatus(str, Enum):
    """Lifecycle status of an execution plan."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class PlanStep:
    """Declarative, immutable definition of a single step within an execution plan.

    Represents 'what should be done' in a plan. Contains zero mutable runtime state.
    """

    step_id: str
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    depends_on: Tuple[str, ...] = field(default_factory=tuple)
    expected_result: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("PlanStep 'step_id' must be a non-empty string.")
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("PlanStep 'tool_name' must be a non-empty string.")
        if not isinstance(self.arguments, dict):
            raise TypeError(f"PlanStep 'arguments' must be a dict, got {type(self.arguments).__name__}.")
        if not isinstance(self.description, str):
            raise TypeError(f"PlanStep 'description' must be a string, got {type(self.description).__name__}.")
        if not isinstance(self.depends_on, (list, tuple)):
            raise TypeError(f"PlanStep 'depends_on' must be a tuple or list, got {type(self.depends_on).__name__}.")

        deps = []
        for dep in self.depends_on:
            if not isinstance(dep, str) or not dep.strip():
                raise ValueError(f"All items in 'depends_on' must be non-empty strings, got {dep!r}.")
            deps.append(dep.strip())

        if self.expected_result is not None and not isinstance(self.expected_result, str):
            raise TypeError(f"PlanStep 'expected_result' must be a string or None, got {type(self.expected_result).__name__}.")

        # Deep freeze to enforce absolute immutability
        object.__setattr__(self, "step_id", self.step_id.strip())
        object.__setattr__(self, "tool_name", self.tool_name.strip())
        object.__setattr__(self, "arguments", dict(self.arguments))
        object.__setattr__(self, "depends_on", tuple(deps))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize plan step to a deterministic JSON-compatible dictionary."""
        d: Dict[str, Any] = {
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "arguments": dict(sorted(self.arguments.items())),
            "description": self.description,
            "depends_on": list(self.depends_on),
        }
        if self.expected_result is not None:
            d["expected_result"] = self.expected_result
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        """Deserialize plan step from dictionary with validation."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict for PlanStep, got {type(data).__name__}.")
        return cls(
            step_id=data.get("step_id", ""),
            tool_name=data.get("tool_name", ""),
            arguments=data.get("arguments", {}) if isinstance(data.get("arguments"), dict) else {},
            description=str(data.get("description", "")),
            depends_on=tuple(data.get("depends_on", ())),
            expected_result=data.get("expected_result"),
        )


@dataclass(frozen=True)
class Plan:
    """Immutable, frozen container representing a complete multi-step execution plan."""

    plan_id: str
    title: str
    description: str
    steps: Tuple[PlanStep, ...] = field(default_factory=tuple)
    overall_risk: RiskLevel = RiskLevel.LOW

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("Plan 'plan_id' must be a non-empty string.")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("Plan 'title' must be a non-empty string.")
        if not isinstance(self.description, str):
            raise TypeError(f"Plan 'description' must be a string, got {type(self.description).__name__}.")
        if not isinstance(self.steps, (list, tuple)):
            raise TypeError(f"Plan 'steps' must be a tuple or list, got {type(self.steps).__name__}.")

        validated_steps: List[PlanStep] = []
        for step in self.steps:
            if not isinstance(step, PlanStep):
                raise TypeError(f"All elements in 'steps' must be PlanStep instances, got {type(step).__name__}.")
            validated_steps.append(step)

        if not isinstance(self.overall_risk, RiskLevel):
            if isinstance(self.overall_risk, str):
                try:
                    object.__setattr__(self, "overall_risk", RiskLevel(self.overall_risk))
                except ValueError:
                    raise ValueError(f"Invalid RiskLevel '{self.overall_risk}'.")
            else:
                raise TypeError(f"Plan 'overall_risk' must be RiskLevel, got {type(self.overall_risk).__name__}.")

        object.__setattr__(self, "plan_id", self.plan_id.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "steps", tuple(validated_steps))

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        """Look up a step by its step_id."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize plan to a deterministic JSON-compatible dictionary."""
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "description": self.description,
            "steps": [step.to_dict() for step in self.steps],
            "overall_risk": self.overall_risk.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        """Deserialize plan from dictionary."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict for Plan, got {type(data).__name__}.")
        steps_raw = data.get("steps", [])
        if not isinstance(steps_raw, (list, tuple)):
            steps_raw = []
        steps = [PlanStep.from_dict(s) if isinstance(s, dict) else s for s in steps_raw]
        risk_raw = data.get("overall_risk", RiskLevel.LOW.value)
        risk = RiskLevel(risk_raw) if isinstance(risk_raw, str) and risk_raw in RiskLevel._value2member_map_ else RiskLevel.LOW
        return cls(
            plan_id=str(data.get("plan_id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            steps=tuple(steps),
            overall_risk=risk,
        )

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)

    def __getitem__(self, index: int) -> PlanStep:
        return self.steps[index]


# ==============================================================================
# RUNTIME OUTCOME MODELS (Explicitly separated from declarative PlanStep)
# ==============================================================================

@dataclass(frozen=True)
class PlanStepExecutionResult:
    """Recorded runtime outcome of executing a single plan step."""

    step_id: str
    tool_name: str
    status: PlanStepStatus
    tool_result: Optional[ToolResult] = None
    error_message: Optional[str] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize step execution outcome to dictionary."""
        return {
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "tool_result": self.tool_result.to_dict() if self.tool_result else None,
            "error_message": self.error_message,
            "duration_seconds": round(self.duration_seconds, 4),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStepExecutionResult":
        """Deserialize step execution outcome from dictionary."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict for PlanStepExecutionResult, got {type(data).__name__}.")
        raw_status = data.get("status", PlanStepStatus.PENDING.value)
        status = PlanStepStatus(raw_status) if raw_status in PlanStepStatus._value2member_map_ else PlanStepStatus.PENDING
        tool_res_raw = data.get("tool_result")
        tool_res = None
        if isinstance(tool_res_raw, dict):
            err_raw = tool_res_raw.get("error")
            err = (
                ToolError(
                    type=str(err_raw.get("type", "UNKNOWN")),
                    message=str(err_raw.get("message", "")),
                    details=dict(err_raw.get("details", {})),
                )
                if isinstance(err_raw, dict)
                else None
            )
            tool_res = ToolResult(
                success=bool(tool_res_raw.get("success", False)),
                tool=str(tool_res_raw.get("tool", "")),
                data=dict(tool_res_raw["data"]) if isinstance(tool_res_raw.get("data"), dict) else None,
                error=err,
            )
        return cls(
            step_id=str(data.get("step_id", "")),
            tool_name=str(data.get("tool_name", "")),
            status=status,
            tool_result=tool_res,
            error_message=data.get("error_message"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
        )


@dataclass(frozen=True)
class PlanExecutionSummary:
    """Aggregated final outcome of executing an entire plan."""

    plan_id: str
    status: PlanStatus
    steps_total: int
    steps_completed: int
    step_results: Tuple[PlanStepExecutionResult, ...] = field(default_factory=tuple)
    failure_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize plan execution summary to dictionary."""
        return {
            "plan_id": self.plan_id,
            "status": self.status.value,
            "steps_total": self.steps_total,
            "steps_completed": self.steps_completed,
            "step_results": [sr.to_dict() for sr in self.step_results],
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanExecutionSummary":
        """Deserialize plan execution summary from dictionary."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict for PlanExecutionSummary, got {type(data).__name__}.")
        raw_status = data.get("status", PlanStatus.DRAFT.value)
        status = PlanStatus(raw_status) if raw_status in PlanStatus._value2member_map_ else PlanStatus.DRAFT
        step_results_raw = data.get("step_results", [])
        step_results = [
            PlanStepExecutionResult.from_dict(sr) if isinstance(sr, dict) else sr
            for sr in step_results_raw
        ]
        return cls(
            plan_id=str(data.get("plan_id", "")),
            status=status,
            steps_total=int(data.get("steps_total", 0)),
            steps_completed=int(data.get("steps_completed", 0)),
            step_results=tuple(step_results),
            failure_reason=data.get("failure_reason"),
        )
