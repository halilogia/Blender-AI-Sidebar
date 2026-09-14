"""M4.2 Task 5 plan review + batch approval tests."""
import unittest
from unittest.mock import MagicMock
from core.types import RiskLevel
from core.events import ProviderResponseReadyEvent
from agent.models import ProviderResponse, ToolCall
from agent.dispatcher import ToolDispatcher
from agent.policy import ApprovalPolicy
from agent.plan_review import build_plan_review
from agent.runtime import AgentRuntime
from tools.registry import ToolRegistry
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.delete_object import DeleteObjectTool
from tools.mutations.transform_object import TransformObjectTool


def _plan():
    return {"title": "T", "description": "D", "steps": [
        {"step_id": "s1", "tool_name": "create_primitive", "arguments": {"primitive_type": "CUBE"}, "description": "make cube"},
        {"step_id": "s2", "tool_name": "delete_object", "arguments": {"name": "Cube"}, "description": "del", "depends_on": ["s1"]},
        {"step_id": "s3", "tool_name": "transform_object", "arguments": {"name": "Cube", "location": [0, 0, 1]}, "description": "move", "depends_on": ["s2"]},
    ]}


def _runtime():
    from core.types import ToolResult
    reg = ToolRegistry()
    reg.register(CreatePrimitiveTool())
    reg.register(DeleteObjectTool())
    reg.register(TransformObjectTool())
    adapter = MagicMock()
    adapter.create_primitive.return_value = ToolResult.ok(tool="create_primitive", data={"name": "Cube"})
    adapter.delete_object.return_value = ToolResult.ok(tool="delete_object", data={"deleted": True})
    adapter.transform_object.return_value = ToolResult.ok(tool="transform_object", data={"ok": True})
    disp = ToolDispatcher(registry=reg, adapter=adapter)
    prov = MagicMock()
    prov.last_tool_calls = []
    rt = AgentRuntime(provider=prov, dispatcher=disp, policy=ApprovalPolicy(), verifier=False, visual_verifier=MagicMock())
    rt.submit_prompt("hi")
    return rt, adapter


def _submit_plan(rt):
    resp = ProviderResponse(assistant_text=None, tool_calls=[ToolCall(call_id="c1", tool_name="propose_plan", arguments=_plan())], is_final=False)
    rt.process_event(ProviderResponseReadyEvent(response=resp, turn_id=rt.current_turn_id))


class TestPlanReview(unittest.TestCase):
    def _reg(self):
        r = ToolRegistry()
        r.register(CreatePrimitiveTool())
        r.register(DeleteObjectTool())
        r.register(TransformObjectTool())
        return r

    def test_review_created(self):
        rv, _ = build_plan_review(_plan(), self._reg(), "turn_1")
        self.assertIsNotNone(rv)
        self.assertEqual(rv.steps_total, 3)

    def test_registry_risk(self):
        rv, _ = build_plan_review(_plan(), self._reg(), "turn_1")
        by = {s.step_id: s.risk_level for s in rv.steps}
        self.assertEqual(by["s1"], RiskLevel.LOW)
        self.assertEqual(by["s2"], RiskLevel.MEDIUM)
        self.assertEqual(rv.overall_risk, RiskLevel.MEDIUM)

    def test_fake_llm_risk_ignored(self):
        p = _plan()
        p["overall_risk"] = "READ_ONLY"
        rv, _ = build_plan_review(p, self._reg(), "turn_1")
        self.assertEqual(rv.overall_risk, RiskLevel.MEDIUM)

    def test_invalid_no_review(self):
        rv, _ = build_plan_review({"title": "", "steps": []}, self._reg(), "t")
        self.assertIsNone(rv)

    def test_no_exec_before_approve(self):
        rt, ad = _runtime()
        _submit_plan(rt)
        self.assertIsNotNone(rt.pending_plan_review)
        ad.create_primitive.assert_not_called()
        ad.delete_object.assert_not_called()

    def test_approve_once(self):
        rt, ad = _runtime()
        _submit_plan(rt)
        aid = rt.pending_plan_review.approval_id
        rt.approve_plan(aid)
        self.assertEqual(ad.create_primitive.call_count, 1)
        self.assertEqual(ad.delete_object.call_count, 1)
        self.assertEqual(ad.transform_object.call_count, 1)
        from agent.policy import NoPendingApprovalError, InvalidApprovalError
        with self.assertRaises((NoPendingApprovalError, InvalidApprovalError)):
            rt.approve_plan(aid)

    def test_reject_zero_mutation(self):
        rt, ad = _runtime()
        _submit_plan(rt)
        rt.reject_plan(rt.pending_plan_review.approval_id)
        ad.create_primitive.assert_not_called()
        ad.delete_object.assert_not_called()
        ad.transform_object.assert_not_called()

    def test_stale_cancel(self):
        rt, _ = _runtime()
        _submit_plan(rt)
        aid = rt.pending_plan_review.approval_id
        rt.cancel_current_turn()
        from agent.policy import NoPendingApprovalError, InvalidApprovalError
        with self.assertRaises((NoPendingApprovalError, InvalidApprovalError)):
            rt.approve_plan(aid)

    def test_immutable(self):
        rt, _ = _runtime()
        _submit_plan(rt)
        plan = rt.pending_plan_review.plan
        with self.assertRaises(Exception):
            plan.steps[0].arguments["primitive_type"] = "SPHERE"

    def test_batch_no_per_step(self):
        rt, ad = _runtime()
        _submit_plan(rt)
        rt.approve_plan(rt.pending_plan_review.approval_id)
        self.assertIsNone(rt.pending_approval)
        self.assertIsNone(rt.pending_plan_review)
        self.assertEqual(ad.delete_object.call_count, 1)
        # No second approval generated during batch execution: queue holds only review events
        queued = rt.event_queue.drain_batch(max_items=50, max_time_sec=0.05)
        kinds = [type(e).__name__ for e in queued]
        self.assertIn("ApprovalRequiredEvent", kinds)

    def test_outside_plan_still_requires_approval(self):
        rt, ad = _runtime()
        _submit_plan(rt)
        rt.approve_plan(rt.pending_plan_review.approval_id)
        self.assertEqual(ad.delete_object.call_count, 1)
        resp = ProviderResponse(assistant_text=None, tool_calls=[ToolCall(call_id="c2", tool_name="delete_object", arguments={"name": "Other"})], is_final=False)
        rt.process_event(ProviderResponseReadyEvent(response=resp, turn_id=rt.current_turn_id))
        self.assertIsNotNone(rt.pending_approval)
        self.assertEqual(ad.delete_object.call_count, 1)

    def test_no_unconditional_hook(self):
        import pathlib
        src = pathlib.Path("agent/runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("or True", src)

    def test_single_tool_intact(self):
        rt, _ = _runtime()
        resp = ProviderResponse(assistant_text=None, tool_calls=[ToolCall(call_id="c", tool_name="delete_object", arguments={"name": "X"})], is_final=False)
        rt.process_event(ProviderResponseReadyEvent(response=resp, turn_id=rt.current_turn_id))
        self.assertIsNotNone(rt.pending_approval)
        self.assertIsNone(rt.pending_plan_review)

    def test_hud_summary(self):
        rv, _ = build_plan_review(_plan(), self._reg(), "t")
        h = rv.hud_summary(max_shown=2)
        self.assertEqual(len(h["steps_shown"]), 2)
        self.assertEqual(h["hidden_count"], 1)

    def test_plan_review_call_id_field(self):
        rv, _ = build_plan_review(_plan(), self._reg(), "turn_1", call_id="call_meta_123")
        self.assertIsNotNone(rv)
        self.assertEqual(rv.call_id, "call_meta_123")
        self.assertEqual(rv.to_dict()["call_id"], "call_meta_123")
        self.assertEqual(rv.hud_summary()["call_id"], "call_meta_123")

    def test_approve_plan_feedback_loop(self):
        import json
        from agent.models import Role
        from agent.state_machine import AgentState
        from agent.policy import NoPendingApprovalError, InvalidApprovalError

        rt, ad = _runtime()
        mock_worker = MagicMock()
        rt.worker = mock_worker
        _submit_plan(rt)  # uses call_id="c1"
        self.assertEqual(rt.pending_plan_review.call_id, "c1")
        aid = rt.pending_plan_review.approval_id

        summary = rt.approve_plan(aid)
        self.assertIsNotNone(summary)

        # 1. Verify TOOL message appended with correct tool_call_id and name
        last_msg = rt.conversation.last()
        self.assertIsNotNone(last_msg)
        self.assertEqual(last_msg.role, Role.TOOL)
        self.assertEqual(last_msg.tool_call_id, "c1")
        self.assertEqual(last_msg.name, "propose_plan")

        # 2. Verify execution summary enters conversation as JSON
        summary_payload = json.loads(last_msg.content)
        self.assertEqual(summary_payload["status"], "COMPLETED")
        self.assertEqual(summary_payload["steps_total"], 3)
        self.assertEqual(summary_payload["steps_completed"], 3)

        # 3. Verify worker is submitted
        mock_worker.submit_task.assert_called_once()
        self.assertEqual(rt.current_state, AgentState.PROCESSING)

        # 4. Verify conversation.validate_sequence() succeeds
        rt.conversation.validate_sequence()

        # 5. Verify same approval cannot be reused
        with self.assertRaises((NoPendingApprovalError, InvalidApprovalError)):
            rt.approve_plan(aid)

    def test_reject_plan_feedback_loop(self):
        import json
        from agent.models import Role
        from agent.state_machine import AgentState
        from agent.policy import NoPendingApprovalError, InvalidApprovalError

        rt, ad = _runtime()
        mock_worker = MagicMock()
        rt.worker = mock_worker
        _submit_plan(rt)  # uses call_id="c1"
        self.assertEqual(rt.pending_plan_review.call_id, "c1")
        aid = rt.pending_plan_review.approval_id

        rt.reject_plan(aid)
        ad.create_primitive.assert_not_called()

        # 1. Verify TOOL message appended with USER_REJECTED
        last_msg = rt.conversation.last()
        self.assertIsNotNone(last_msg)
        self.assertEqual(last_msg.role, Role.TOOL)
        self.assertEqual(last_msg.tool_call_id, "c1")
        self.assertEqual(last_msg.name, "propose_plan")

        # 2. Verify controlled JSON carries USER_REJECTED
        rej_payload = json.loads(last_msg.content)
        self.assertEqual(rej_payload["status"], "USER_REJECTED")
        self.assertIn("USER_REJECTED", last_msg.content)

        # 3. Verify worker is submitted
        mock_worker.submit_task.assert_called_once()
        self.assertEqual(rt.current_state, AgentState.PROCESSING)

        # 4. Verify conversation.validate_sequence() succeeds
        rt.conversation.validate_sequence()

        # 5. Verify same approval cannot be reused
        with self.assertRaises((NoPendingApprovalError, InvalidApprovalError)):
            rt.reject_plan(aid)

    def test_reject_plan_wrong_id_and_stale(self):
        from agent.policy import InvalidApprovalError, NoPendingApprovalError

        rt, _ = _runtime()
        _submit_plan(rt)
        with self.assertRaises(InvalidApprovalError):
            rt.reject_plan("wrong_approval_id")

        # Cancelled turn
        aid = rt.pending_plan_review.approval_id
        rt.cancel_current_turn()
        with self.assertRaises((NoPendingApprovalError, InvalidApprovalError)):
            rt.reject_plan(aid)

        # Stale turn
        rt2, _ = _runtime()
        _submit_plan(rt2)
        aid2 = rt2.pending_plan_review.approval_id
        rt2._current_turn_id = "turn_different"
        with self.assertRaises(InvalidApprovalError):
            rt2.reject_plan(aid2)


class TestPlanRepairBudget(unittest.TestCase):
    def test_new_turn_counter_zero(self):
        rt, _ = _runtime()
        self.assertEqual(rt.current_plan_repairs, 0)
        self.assertEqual(rt._current_plan_repairs, 0)

    def test_successful_first_plan_does_not_increment_repairs(self):
        rt, ad = _runtime()
        _submit_plan(rt)
        aid = rt.pending_plan_review.approval_id
        rt.approve_plan(aid)
        self.assertEqual(rt._current_plan_repairs, 0)

    def test_first_plan_failed_allows_one_repair(self):
        rt, ad = _runtime()
        from core.types import ToolResult
        ad.create_primitive.return_value = ToolResult.fail(
            tool="create_primitive",
            error_type="BLENDER_FAIL",
            message="Cannot create primitive",
        )
        _submit_plan(rt)
        aid = rt.pending_plan_review.approval_id
        summary = rt.approve_plan(aid)
        self.assertEqual(summary.status.value, "FAILED")
        self.assertEqual(rt._current_plan_repairs, 0)

        # Propose repair plan (uses call_id="c2")
        resp = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c2", tool_name="propose_plan", arguments=_plan())],
            is_final=False,
        )
        rt.process_event(ProviderResponseReadyEvent(response=resp, turn_id=rt.current_turn_id))

        # Repair plan is created and requires approval (no bypass!)
        from agent.state_machine import AgentState
        self.assertIsNotNone(rt.pending_plan_review)
        self.assertEqual(rt.current_state, AgentState.PENDING_APPROVAL)
        self.assertEqual(rt.pending_plan_review.call_id, "c2")
        # Repair counter is now 1
        self.assertEqual(rt._current_plan_repairs, 1)

    def test_repair_plan_failed_blocks_further_repairs_with_max_repairs_exceeded(self):
        rt, ad = _runtime()
        from core.types import ToolResult
        from agent.state_machine import AgentState

        # Step 1: Initial plan fails
        ad.create_primitive.return_value = ToolResult.fail(
            tool="create_primitive",
            error_type="BLENDER_FAIL",
            message="Cannot create primitive",
        )
        _submit_plan(rt)
        aid1 = rt.pending_plan_review.approval_id
        rt.approve_plan(aid1)
        self.assertEqual(rt._current_plan_repairs, 0)

        # Step 2: Propose repair plan 1
        resp2 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c2", tool_name="propose_plan", arguments=_plan())],
            is_final=False,
        )
        rt.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=rt.current_turn_id))
        self.assertEqual(rt._current_plan_repairs, 1)
        self.assertIsNotNone(rt.pending_plan_review)

        # Step 3: Repair plan 1 also fails
        aid2 = rt.pending_plan_review.approval_id
        rt.approve_plan(aid2)
        self.assertEqual(rt._current_plan_repairs, 1)

        # Step 4: LLM attempts to propose repair plan 2 (exceeding max_plan_repairs=1)
        resp3 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c3", tool_name="propose_plan", arguments=_plan())],
            is_final=False,
        )
        err_res = rt.process_event(ProviderResponseReadyEvent(response=resp3, turn_id=rt.current_turn_id))

        # Verification:
        # Error result produced
        self.assertIsNotNone(err_res)
        self.assertEqual(err_res.state, AgentState.ERROR.value)
        self.assertIn("Maximum plan repairs limit reached", err_res.final_text)

        # State is ERROR
        self.assertEqual(rt.current_state, AgentState.ERROR)
        self.assertIsNone(rt.pending_plan_review)

        # Event queue has AgentErrorEvent with MAX_PLAN_REPAIRS_EXCEEDED
        queued = rt.event_queue.drain_batch(max_items=50, max_time_sec=0.05)
        err_events = [e for e in queued if getattr(e, "error_type", None) == "MAX_PLAN_REPAIRS_EXCEEDED"]
        self.assertTrue(len(err_events) >= 1)

        # Conversation sequence integrity is preserved
        rt.conversation.validate_sequence()

        # Last tool message carries MAX_PLAN_REPAIRS_EXCEEDED
        last_msg = rt.conversation.last()
        self.assertEqual(last_msg.role.value, "tool")
        self.assertEqual(last_msg.tool_call_id, "c3")
        self.assertIn("MAX_PLAN_REPAIRS_EXCEEDED", last_msg.content)

    def test_new_user_turn_resets_repair_counter(self):
        rt, ad = _runtime()
        from core.types import ToolResult

        # Turn 1: Initial plan fails and repair plan 1 is proposed (counter becomes 1)
        ad.create_primitive.return_value = ToolResult.fail(
            tool="create_primitive",
            error_type="BLENDER_FAIL",
            message="Fail",
        )
        _submit_plan(rt)
        aid1 = rt.pending_plan_review.approval_id
        rt.approve_plan(aid1)

        resp2 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c2", tool_name="propose_plan", arguments=_plan())],
            is_final=False,
        )
        rt.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=rt.current_turn_id))
        self.assertEqual(rt._current_plan_repairs, 1)

        # Turn 2: User submits a new prompt after cancelling or resetting
        rt.cancel_current_turn()
        self.assertEqual(rt._current_plan_repairs, 0)
        rt.submit_prompt("brand new task")
        self.assertEqual(rt._current_plan_repairs, 0)

    def test_max_tool_rounds_takes_precedence_or_harmonizes(self):
        rt, ad = _runtime()
        rt.max_tool_rounds = 1  # only 1 tool round allowed
        # First round uses tool round 1
        _submit_plan(rt)
        aid = rt.pending_plan_review.approval_id
        from core.types import ToolResult
        ad.create_primitive.return_value = ToolResult.fail(tool="create_primitive", error_type="E", message="M")
        rt.approve_plan(aid)

        # When worker/LLM tries round 2, max_tool_rounds trips
        resp2 = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c2", tool_name="propose_plan", arguments=_plan())],
            is_final=False,
        )
        err = rt.process_event(ProviderResponseReadyEvent(response=resp2, turn_id=rt.current_turn_id))
        self.assertIsNotNone(err)
        self.assertIn("Maximum tool rounds limit reached", err.final_text)

    def test_stale_and_cancelled_turn_isolation(self):
        rt, _ = _runtime()
        _submit_plan(rt)
        self.assertEqual(rt._current_plan_repairs, 0)

        # Stale event with wrong turn_id has no effect
        resp_stale = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="c_stale", tool_name="propose_plan", arguments=_plan())],
            is_final=False,
        )
        rt.process_event(ProviderResponseReadyEvent(response=resp_stale, turn_id="turn_wrong"))
        self.assertEqual(rt._current_plan_repairs, 0)
        self.assertEqual(rt.stale_events_count, 1)

        # Cancel turn resets counter
        rt.cancel_current_turn()
        self.assertEqual(rt._current_plan_repairs, 0)


if __name__ == "__main__":
    unittest.main()

