"""High-level plan review + batch approval for validated execution plans.

Zero Blender (bpy) dependencies. Pure Python standard library.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from core.types import RiskLevel
from agent.plan_models import Plan, max_risk_level
from agent.plan_validator import PlanValidator
from agent.policy import ApprovalPolicy
from tools.registry import ToolRegistry

MAX_REVIEW_STEPS_SHOWN: int = 5


@dataclass(frozen=True)
class PlanReviewStep:
    step_id: str
    tool_name: str
    description: str
    risk_level: RiskLevel


@dataclass(frozen=True)
class PlanReview:
    approval_id: str
    turn_id: str
    plan: Plan
    title: str
    description: str
    steps_total: int
    steps: Tuple[PlanReviewStep, ...] = ()
    overall_risk: RiskLevel = RiskLevel.LOW
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "plan",
            "approval_id": self.approval_id,
            "turn_id": self.turn_id,
            "plan_id": self.plan.plan_id,
            "title": self.title,
            "description": self.description,
            "steps_total": self.steps_total,
            "overall_risk": self.overall_risk.value,
            "steps": [
                {"step_id": s.step_id, "tool_name": s.tool_name,
                 "description": s.description, "risk_level": s.risk_level.value}
                for s in self.steps
            ],
        }

    def hud_summary(self, max_shown: int = MAX_REVIEW_STEPS_SHOWN) -> Dict[str, Any]:
        shown = [
            {"step_id": s.step_id, "tool_name": s.tool_name,
             "description": s.description, "risk_level": s.risk_level.value}
            for s in self.steps[:max_shown]
        ]
        return {
            "kind": "plan",
            "approval_id": self.approval_id,
            "title": self.title,
            "description": f"Plan '{self.title}' ({self.steps_total} steps)",
            "steps_total": self.steps_total,
            "steps_shown": shown,
            "hidden_count": max(0, self.steps_total - len(shown)),
            "overall_risk": self.overall_risk.value,
            "risk_level": self.overall_risk.value,
            "tool_name": "plan",
        }


def build_plan_review(
    raw_plan: Union[Plan, Dict[str, Any], str],
    registry: ToolRegistry,
    turn_id: str,
    policy: Optional[ApprovalPolicy] = None,
) -> Tuple[Optional[PlanReview], str]:
    validator = PlanValidator(registry)
    result = validator.validate(raw_plan)
    if not result.valid or result.plan is None:
        return None, f"Plan validation failed: {result.error_message}"
    plan = result.plan
    policy = policy or ApprovalPolicy()
    review_steps: List[PlanReviewStep] = []
    risks: List[RiskLevel] = []
    for step in plan.steps:
        tool = registry.get(step.tool_name) if registry.exists(step.tool_name) else None
        risk = getattr(tool, "risk_level", RiskLevel.HIGH) if tool else RiskLevel.HIGH
        if isinstance(risk, str):
            try:
                risk = RiskLevel(risk)
            except ValueError:
                risk = RiskLevel.HIGH
        risks.append(risk)
        desc = step.description.strip() if step.description else policy.create_human_description(
            step.tool_name, dict(step.arguments))
        review_steps.append(PlanReviewStep(
            step_id=step.step_id, tool_name=step.tool_name,
            description=desc, risk_level=risk))
    overall = max_risk_level(risks)
    review = PlanReview(
        approval_id=f"plan_{uuid.uuid4().hex[:10]}",
        turn_id=turn_id,
        plan=plan,
        title=plan.title,
        description=plan.description,
        steps_total=len(plan.steps),
        steps=tuple(review_steps),
        overall_risk=overall,
        created_at=time.time(),
    )
    return review, ""
