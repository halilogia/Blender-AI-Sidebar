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


if __name__ == "__main__":
    unittest.main()

