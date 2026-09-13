"""Deterministic execution engine for multi-step plans.

Executes validated Plan instances strictly in topological order with fail-fast
dependency gating, step outcome recording, and verification preservation.

Zero Blender (bpy) dependencies. Pure Python standard library.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from core.types import RiskLevel, ToolResult
from agent.models import ToolCall
from agent.plan_models import (
    Plan,
    PlanStep,
    PlanStepStatus,
    PlanStatus,
    PlanStepExecutionResult,
    PlanExecutionSummary,
)
from agent.plan_validator import PlanValidator
from agent.policy import ApprovalDecision, ApprovalPolicy
from agent.dispatcher import ToolDispatcher
from tools.base import BaseTool
from tools.registry import ToolRegistry


class PlanExecutor:
    """Deterministic execution engine for multi-step execution plans."""

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: Optional[ToolDispatcher] = None,
        runtime: Optional[Any] = None,
        verifier: Optional[Any] = None,
        policy: Optional[ApprovalPolicy] = None,
        execute_fn: Optional[Callable[[ToolCall, Optional[str]], ToolResult]] = None,
        approval_hook: Optional[Callable[[PlanStep, BaseTool], bool]] = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError(f"PlanExecutor requires ToolRegistry, got {type(registry).__name__}.")

        self.registry = registry
        self.dispatcher = dispatcher
        self.runtime = runtime
        self.verifier = verifier
        self.policy = policy
        self.execute_fn = execute_fn
        self.approval_hook = approval_hook
        self.validator = PlanValidator(registry)

    def _execute_step(
        self,
        step: PlanStep,
        tool: BaseTool,
    ) -> ToolResult:
        """Dispatch a single plan step through configured execution engine.

        Preserves semantic verification and visual verification pipelines.
        """
        # Strictly forbid executing propose_plan as a step
        if step.tool_name == "propose_plan":
            return ToolResult.fail(
                tool=step.tool_name,
                error_type="PROPOSE_PLAN_NESTING_FORBIDDEN",
                message=f"Step '{step.step_id}': Tool 'propose_plan' cannot be executed as a plan step.",
            )

        tool_call = ToolCall(
            call_id=f"call_{step.step_id}",
            tool_name=step.tool_name,
            arguments=dict(step.arguments),
        )

        # 1. Custom execute_fn takes precedence if provided (used in targeted unit tests)
        if self.execute_fn is not None:
            return self.execute_fn(tool_call, step.expected_result)

        # 2. AgentRuntime handles dispatch + full verification pipeline on main thread
        if self.runtime is not None and hasattr(self.runtime, "execute_tool_call"):
            return self.runtime.execute_tool_call(
                tool_call=tool_call,
                expected_visual_description=step.expected_result,
            )

        # 3. ToolDispatcher handles argument validation and adapter execution
        if self.dispatcher is not None:
            res = self.dispatcher.dispatch(tool_call)

            # Apply semantic change verification if verifier is configured
            if res.success and self.verifier is not None:
                from agent.verifier import build_change_set_from_result

                change_set = build_change_set_from_result(
                    tool_name=step.tool_name,
                    arguments=dict(step.arguments),
                    result_data=res.data or {},
                )
                if change_set is not None:
                    verif_result = self.verifier.verify(change_set)
                    if not verif_result.passed:
                        verif_dict = verif_result.to_dict()
                        return ToolResult.fail(
                            tool=res.tool,
                            error_type="VERIFICATION_FAILED",
                            message=verif_result.summary,
                            details={
                                "verification": verif_dict,
                                "mismatches": verif_result.mismatches,
                            },
                            data={"verification": verif_dict},
                        )
                    # Preserve verification outcome in result data
                    data = dict(res.data or {})
                    data["verification"] = verif_result.to_dict()
                    return ToolResult.ok(tool=res.tool, data=data)

            return res

        return ToolResult.fail(
            tool=step.tool_name,
            error_type="NO_DISPATCHER",
            message=f"No dispatcher, runtime, or execute_fn available to execute step '{step.step_id}'.",
        )

    def execute_plan(
        self,
        raw_plan: Union[Plan, Dict[str, Any], str],
    ) -> PlanExecutionSummary:
        """Validate and execute a multi-step plan deterministically in topological order.

        Args:
            raw_plan: Plan instance, dictionary, or JSON string.

        Returns:
            PlanExecutionSummary with final status, steps completed count, and step outcomes.
        """
        # 1. Strictly validate plan using PlanValidator
        validation_result = self.validator.validate(raw_plan)
        if not validation_result.valid or validation_result.plan is None:
            plan_id = "unvalidated_plan"
            steps_total = 0
            if isinstance(raw_plan, Plan):
                plan_id = raw_plan.plan_id
                steps_total = len(raw_plan.steps)
            elif isinstance(raw_plan, dict):
                plan_id = str(raw_plan.get("plan_id", "unvalidated_plan"))
                raw_steps = raw_plan.get("steps")
                if isinstance(raw_steps, (list, tuple)):
                    steps_total = len(raw_steps)

            return PlanExecutionSummary(
                plan_id=plan_id,
                status=PlanStatus.FAILED,
                steps_total=steps_total,
                steps_completed=0,
                step_results=(),
                failure_reason=f"Plan validation failed: {validation_result.error_message}",
            )

        plan = validation_result.plan

        # 2. Prevent recursion: propose_plan must not be an execution step
        for step in plan.steps:
            if step.tool_name == "propose_plan":
                return PlanExecutionSummary(
                    plan_id=plan.plan_id,
                    status=PlanStatus.FAILED,
                    steps_total=len(plan.steps),
                    steps_completed=0,
                    step_results=(),
                    failure_reason=f"Execution rejected: Step '{step.step_id}' references forbidden meta-tool 'propose_plan'.",
                )

        # 3. Execute steps in topological order
        completed_step_ids: Set[str] = set()
        step_results: List[PlanStepExecutionResult] = []
        overall_failure_reason: Optional[str] = None
        has_failed: bool = False

        for step in plan.steps:
            # If a prior step failed, fail-fast and skip all remaining steps
            if has_failed:
                step_results.append(
                    PlanStepExecutionResult(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status=PlanStepStatus.SKIPPED,
                        error_message="Skipped due to failure in preceding plan step.",
                    )
                )
                continue

            # Check dependency satisfaction: all depends_on steps must be in completed_step_ids
            unmet_deps = [dep for dep in step.depends_on if dep not in completed_step_ids]
            if unmet_deps:
                has_failed = True
                overall_failure_reason = (
                    f"Step '{step.step_id}' dependencies not satisfied: unmet {unmet_deps}."
                )
                step_results.append(
                    PlanStepExecutionResult(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status=PlanStepStatus.SKIPPED,
                        error_message=overall_failure_reason,
                    )
                )
                continue

            # Resolve tool from registry
            if not self.registry.exists(step.tool_name):
                has_failed = True
                overall_failure_reason = (
                    f"Step '{step.step_id}': Tool '{step.tool_name}' not found in ToolRegistry."
                )
                step_results.append(
                    PlanStepExecutionResult(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status=PlanStepStatus.FAILED,
                        error_message=overall_failure_reason,
                    )
                )
                continue

            tool = self.registry.get(step.tool_name)

            # Check approval boundary if policy is configured
            if self.policy is not None:
                step_call = ToolCall(
                    call_id=f"call_{step.step_id}",
                    tool_name=step.tool_name,
                    arguments=dict(step.arguments),
                )
                decision = self.policy.evaluate(tool, step_call)
                if decision == ApprovalDecision.REQUIRE_APPROVAL:
                    if self.approval_hook is not None:
                        is_approved = self.approval_hook(step, tool)
                        if not is_approved:
                            has_failed = True
                            overall_failure_reason = f"Step '{step.step_id}' was rejected by approval gate."
                            step_results.append(
                                PlanStepExecutionResult(
                                    step_id=step.step_id,
                                    tool_name=step.tool_name,
                                    status=PlanStepStatus.CANCELLED,
                                    error_message=overall_failure_reason,
                                )
                            )
                            continue

            # Execute step on main thread
            t0 = time.time()
            try:
                res = self._execute_step(step, tool)
            except Exception as exc:
                res = ToolResult.fail(
                    tool=step.tool_name,
                    error_type="STEP_EXECUTION_EXCEPTION",
                    message=f"Unhandled exception during step '{step.step_id}': {str(exc)}",
                )
            duration = max(0.0, time.time() - t0)

            if res.success:
                completed_step_ids.add(step.step_id)
                step_results.append(
                    PlanStepExecutionResult(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status=PlanStepStatus.COMPLETED,
                        tool_result=res,
                        duration_seconds=duration,
                    )
                )
            else:
                has_failed = True
                err_msg = res.error.message if res.error else "Step execution failed"
                overall_failure_reason = f"Step '{step.step_id}' failed: {err_msg}"
                step_results.append(
                    PlanStepExecutionResult(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status=PlanStepStatus.FAILED,
                        tool_result=res,
                        error_message=err_msg,
                        duration_seconds=duration,
                    )
                )

        final_status = PlanStatus.FAILED if has_failed else PlanStatus.COMPLETED
        return PlanExecutionSummary(
            plan_id=plan.plan_id,
            status=final_status,
            steps_total=len(plan.steps),
            steps_completed=len(completed_step_ids),
            step_results=tuple(step_results),
            failure_reason=overall_failure_reason,
        )
