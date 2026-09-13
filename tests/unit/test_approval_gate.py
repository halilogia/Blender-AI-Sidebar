"""Unit tests for M4.1 Deterministic Approval Gate & Policy.

Validates the 12 acceptance criteria:
1. LOW risk -> auto-approve / execute
2. MEDIUM risk -> pending approval
3. HIGH risk -> pending approval
4. CRITICAL risk -> pending approval
5. approve -> tool executes exactly once
6. reject -> tool never executes
7. duplicate approve -> rejected, no second execution
8. invalid approval_id -> rejected
9. stale turn approval -> rejected
10. cancellation -> pending approval invalidated
11. tool arguments immutable during approval lifecycle
12. provider lack of confirmation cannot bypass execution gate

Zero Blender dependencies. Pure Python.
"""

import copy
import os
import sys
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.events import (
    ApprovalRequiredEvent,
    ApprovalResolvedEvent,
    EventType,
    ProviderResponseReadyEvent,
)
from core.event_queue import ThreadSafeEventQueue
from core.types import RiskLevel, ToolResult
from agent.dispatcher import ToolDispatcher
from agent.models import ProviderResponse, ToolCall
from agent.policy import (
    ApprovalDecision,
    ApprovalPolicy,
    InvalidApprovalError,
    NoPendingApprovalError,
    PendingApproval,
)
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from tools.base import BaseTool
from tools.registry import ToolRegistry


class LowDummyTool(BaseTool):
    name = "low_tool"
    description = "Low risk dummy tool"
    risk_level = RiskLevel.LOW
    input_schema = {"type": "object", "properties": {"param": {"type": "string"}}}

    def __init__(self):
        self.execution_count = 0
        self.last_kwargs = {}

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        self.execution_count += 1
        self.last_kwargs = kwargs
        return ToolResult.ok(self.name, {"executed": True, "count": self.execution_count})


class DeleteObjectDummyTool(BaseTool):
    name = "delete_object"
    description = "Delete object medium risk tool"
    risk_level = RiskLevel.MEDIUM
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    def __init__(self):
        self.execution_count = 0
        self.last_kwargs = {}

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        self.execution_count += 1
        self.last_kwargs = kwargs
        return ToolResult.ok(self.name, {"deleted": True, "count": self.execution_count})


class HighDummyTool(BaseTool):
    name = "high_tool"
    description = "High risk dummy tool"
    risk_level = RiskLevel.HIGH
    input_schema = {"type": "object", "properties": {}}

    def __init__(self):
        self.execution_count = 0

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        self.execution_count += 1
        return ToolResult.ok(self.name, {"executed": True})


class CriticalDummyTool(BaseTool):
    name = "critical_tool"
    description = "Critical risk dummy tool"
    risk_level = RiskLevel.CRITICAL
    input_schema = {"type": "object", "properties": {}}

    def __init__(self):
        self.execution_count = 0

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        self.execution_count += 1
        return ToolResult.ok(self.name, {"executed": True})


class ReadOnlyDummyTool(BaseTool):
    name = "inspect_scene"
    description = "Read-only dummy tool"
    risk_level = RiskLevel.READ_ONLY
    input_schema = {"type": "object", "properties": {}}

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, {"inspected": True})


class TestApprovalPolicyEvaluation(unittest.TestCase):
    """Test policy evaluation across all risk levels."""

    def setUp(self):
        self.policy = ApprovalPolicy()

    def test_criterion_1_low_and_readonly_auto_approve(self):
        """READ_ONLY and LOW risk tools auto-approve without user confirmation."""
        tool_ro = ReadOnlyDummyTool()
        tool_low = LowDummyTool()
        call = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})

        self.assertEqual(self.policy.evaluate(tool_ro, call), ApprovalDecision.AUTO_APPROVE)
        self.assertEqual(self.policy.evaluate(tool_low, call), ApprovalDecision.AUTO_APPROVE)

    def test_criterion_2_medium_requires_approval(self):
        """MEDIUM risk tools (e.g. delete_object) require explicit user approval."""
        tool_med = DeleteObjectDummyTool()
        call = ToolCall(call_id="call_2", tool_name="delete_object", arguments={"name": "Cube"})

        self.assertEqual(self.policy.evaluate(tool_med, call), ApprovalDecision.REQUIRE_APPROVAL)

    def test_criterion_3_high_requires_approval(self):
        """HIGH risk tools require explicit user approval."""
        tool_high = HighDummyTool()
        call = ToolCall(call_id="call_3", tool_name="high_tool", arguments={})

        self.assertEqual(self.policy.evaluate(tool_high, call), ApprovalDecision.REQUIRE_APPROVAL)

    def test_criterion_4_critical_requires_approval(self):
        """CRITICAL risk tools require explicit user approval."""
        tool_crit = CriticalDummyTool()
        call = ToolCall(call_id="call_4", tool_name="critical_tool", arguments={})

        self.assertEqual(self.policy.evaluate(tool_crit, call), ApprovalDecision.REQUIRE_APPROVAL)

    def test_unregistered_tool_fails_safe_to_require_approval(self):
        """Unknown or unregistered tools default safely to requiring approval."""
        call = ToolCall(call_id="call_unreg", tool_name="unknown_tool", arguments={})
        self.assertEqual(self.policy.evaluate(None, call), ApprovalDecision.REQUIRE_APPROVAL)


class TestApprovalGateRuntimeExecution(unittest.TestCase):
    """Test AgentRuntime execution gating, approve/reject lifecycle, and security invariants."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.low_tool = LowDummyTool()
        self.delete_tool = DeleteObjectDummyTool()
        self.high_tool = HighDummyTool()

        self.registry.register(self.low_tool)
        self.registry.register(self.delete_tool)
        self.registry.register(self.high_tool)

        self.mock_adapter = MagicMock()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.mock_adapter)
        self.mock_provider = MagicMock()
        self.mock_worker = MagicMock()
        self.event_queue = ThreadSafeEventQueue()

        self.runtime = AgentRuntime(
            provider=self.mock_provider,
            dispatcher=self.dispatcher,
            event_queue=self.event_queue,
            worker=self.mock_worker,
        )

    def _simulate_provider_tool_response(self, tool_call: ToolCall, turn_id: str = "turn_1"):
        """Helper to simulate provider emitting a tool call into process_event."""
        self.runtime._current_turn_id = turn_id
        self.runtime.state_machine.reset()
        self.runtime.state_machine.transition_to(AgentState.PROCESSING)

        resp = ProviderResponse(
            assistant_text=None,
            tool_calls=[tool_call],
            is_final=False,
        )
        event = ProviderResponseReadyEvent(response=resp, turn_id=turn_id)
        return self.runtime.process_event(event)

    def test_criterion_1_low_risk_executes_immediately(self):
        """LOW risk tool call passes through gate and executes immediately without pending approval."""
        call = ToolCall(call_id="c_low", tool_name="low_tool", arguments={"param": "val"})
        self._simulate_provider_tool_response(call, turn_id="turn_low")

        self.assertEqual(self.low_tool.execution_count, 1)
        self.assertIsNone(self.runtime.pending_approval)
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

    def test_criterion_2_medium_risk_enters_pending_approval(self):
        """MEDIUM risk tool call enters PENDING_APPROVAL and does NOT execute."""
        call = ToolCall(call_id="c_del", tool_name="delete_object", arguments={"name": "Cube"})
        self._simulate_provider_tool_response(call, turn_id="turn_del")

        self.assertEqual(self.delete_tool.execution_count, 0)
        self.assertIsNotNone(self.runtime.pending_approval)
        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertEqual(self.runtime.pending_approval.tool_name, "delete_object")
        self.assertEqual(self.runtime.pending_approval.risk_level, RiskLevel.MEDIUM)
        self.assertEqual(self.runtime.pending_approval.human_readable_description, 'Delete "Cube"')

    def test_criterion_5_approve_executes_tool_exactly_once(self):
        """Approving pending tool executes it exactly once and dispatches next worker step."""
        call = ToolCall(call_id="c_del", tool_name="delete_object", arguments={"name": "Cube"})
        self._simulate_provider_tool_response(call, turn_id="turn_appr")

        approval_id = self.runtime.pending_approval.approval_id
        self.assertEqual(self.delete_tool.execution_count, 0)

        # Call approve
        self.runtime.approve(approval_id)

        self.assertEqual(self.delete_tool.execution_count, 1)
        self.assertIsNone(self.runtime.pending_approval)
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)
        self.mock_worker.submit_task.assert_called_once()

    def test_criterion_6_reject_prevents_tool_execution(self):
        """Rejecting pending tool guarantees tool is NEVER executed and informs LLM via ToolResult."""
        call = ToolCall(call_id="c_del", tool_name="delete_object", arguments={"name": "Cube"})
        self._simulate_provider_tool_response(call, turn_id="turn_rej")

        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.reject(approval_id)

        # Tool was NEVER dispatched
        self.assertEqual(self.delete_tool.execution_count, 0)
        self.assertIsNone(self.runtime.pending_approval)
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

        # Verify a controlled USER_REJECTED ToolResult was created
        last_res = self.runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "USER_REJECTED")

        # Worker was informed of rejection
        self.mock_worker.submit_task.assert_called_once()

    def test_criterion_7_duplicate_approve_prevented(self):
        """Calling approve a second time raises NoPendingApprovalError and prevents double execution."""
        call = ToolCall(call_id="c_del", tool_name="delete_object", arguments={"name": "Cube"})
        self._simulate_provider_tool_response(call, turn_id="turn_dup")

        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.approve(approval_id)
        self.assertEqual(self.delete_tool.execution_count, 1)

        # Second approve attempt
        with self.assertRaises(NoPendingApprovalError):
            self.runtime.approve(approval_id)

        # Execution count remains exactly 1
        self.assertEqual(self.delete_tool.execution_count, 1)

    def test_criterion_8_invalid_approval_id_rejected(self):
        """Providing an invalid or mismatched approval_id raises InvalidApprovalError."""
        call = ToolCall(call_id="c_del", tool_name="delete_object", arguments={"name": "Cube"})
        self._simulate_provider_tool_response(call, turn_id="turn_inv")

        with self.assertRaises(InvalidApprovalError):
            self.runtime.approve("appr_non_existent_id")

        with self.assertRaises(InvalidApprovalError):
            self.runtime.reject("appr_non_existent_id")

        self.assertEqual(self.delete_tool.execution_count, 0)
        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)

    def test_criterion_9_stale_turn_approval_rejected(self):
        """An approval belonging to an older turn is rejected if active turn changed."""
        call = ToolCall(call_id="c_del", tool_name="delete_object", arguments={"name": "Cube"})
        self._simulate_provider_tool_response(call, turn_id="turn_old")

        approval_id = self.runtime.pending_approval.approval_id
        # Simulate turn change
        self.runtime._current_turn_id = "turn_new"

        with self.assertRaises(InvalidApprovalError):
            self.runtime.approve(approval_id)

        self.assertEqual(self.delete_tool.execution_count, 0)

    def test_criterion_10_cancellation_invalidates_pending_approval(self):
        """Cancelling turn clears pending approval and resets state to IDLE."""
        call = ToolCall(call_id="c_del", tool_name="delete_object", arguments={"name": "Cube"})
        self._simulate_provider_tool_response(call, turn_id="turn_cancel")

        approval_id = self.runtime.pending_approval.approval_id
        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)

        # Cancel turn
        self.runtime.cancel_current_turn()

        self.assertIsNone(self.runtime.pending_approval)
        self.assertEqual(self.runtime.current_state, AgentState.IDLE)

        # Any subsequent approve or reject fails
        with self.assertRaises(NoPendingApprovalError):
            self.runtime.approve(approval_id)

        self.assertEqual(self.delete_tool.execution_count, 0)

    def test_criterion_11_tool_arguments_immutable_during_approval(self):
        """PendingApproval freezes arguments; caller modifications do not affect dispatched arguments."""
        mutable_args = {"name": "Cube", "extra": "data"}
        call = ToolCall(call_id="c_imm", tool_name="delete_object", arguments=mutable_args)
        self._simulate_provider_tool_response(call, turn_id="turn_imm")

        # Mutate the original dictionary after submission
        mutable_args["name"] = "AttackerModifiedCube"
        mutable_args["malicious_param"] = 123

        # Verify PendingApproval retained the frozen snapshot
        self.assertEqual(self.runtime.pending_approval.tool_call.arguments["name"], "Cube")
        self.assertNotIn("malicious_param", self.runtime.pending_approval.tool_call.arguments)

        # Approve and verify dispatched arguments
        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.approve(approval_id)
        self.assertEqual(self.delete_tool.last_kwargs.get("name"), "Cube")
        self.assertNotIn("malicious_param", self.delete_tool.last_kwargs)

    def test_criterion_12_provider_cannot_bypass_approval_gate(self):
        """Even if provider claims action was done or requests tool directly, gate always intercepts."""
        # Simulated scenario: User prompt 'Küpü sil'
        # Provider directly emits delete_object without asking user
        direct_delete_call = ToolCall(
            call_id="c_direct",
            tool_name="delete_object",
            arguments={"name": "TargetCube"},
        )
        self._simulate_provider_tool_response(direct_delete_call, turn_id="turn_bypass_attempt")

        # Gate intercepted it deterministically
        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertEqual(self.delete_tool.execution_count, 0)
        self.assertIsNotNone(self.runtime.pending_approval)
        self.assertEqual(self.runtime.pending_approval.tool_name, "delete_object")


if __name__ == "__main__":
    unittest.main()
