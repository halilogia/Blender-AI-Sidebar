"""Unit tests for M5 Görev 3: Execute -> Verify Runtime Integration.

Validates that AgentRuntime automatically verifies mutation outcomes, handles PASS/FAIL,
attaches structured verification metadata to ToolResult, and respects approval/reject gating.
Pure Python. Zero Blender (bpy) dependencies.
"""

import unittest
from unittest.mock import MagicMock

from core.event_queue import ThreadSafeEventQueue
from core.events import AgentErrorEvent, ProviderResponseReadyEvent
from core.types import RiskLevel, ToolResult
from agent.dispatcher import ToolDispatcher
from agent.models import ProviderResponse, ToolCall
from agent.policy import ApprovalPolicy
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from agent.verifier import ChangeVerifier
from tools.base import BaseTool
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.delete_object import DeleteObjectTool
from tools.mutations.transform_object import TransformObjectTool
from tools.registry import ToolRegistry


class DummySceneTool(BaseTool):
    name = "inspect_scene"
    description = "Read-only dummy tool"
    risk_level = RiskLevel.READ_ONLY
    input_schema = {"type": "object", "properties": {}}

    def execute(self, adapter: any, **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, {"scene_name": "TestScene", "objects": ["Cube"]})


class TestRuntimeVerification(unittest.TestCase):
    """Test verification engine integrated into AgentRuntime execution loop."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(TransformObjectTool())
        self.registry.register(DeleteObjectTool())
        self.registry.register(DummySceneTool())

        self.mock_adapter = MagicMock()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.mock_adapter)
        self.mock_provider = MagicMock()
        self.mock_worker = MagicMock()
        self.event_queue = ThreadSafeEventQueue()
        self.policy = ApprovalPolicy()
        self.verifier = ChangeVerifier()

        self.runtime = AgentRuntime(
            provider=self.mock_provider,
            dispatcher=self.dispatcher,
            event_queue=self.event_queue,
            worker=self.mock_worker,
            policy=self.policy,
            verifier=self.verifier,
        )

    def _simulate_tool_call_event(self, tool_call: ToolCall, turn_id: str = "turn_v1"):
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

    def test_low_risk_create_primitive_auto_executes_and_verifies_pass(self):
        """LOW risk create_primitive auto-executes, verifies actual snapshot, and attaches PASS verification."""
        self.mock_adapter.create_primitive.return_value = ToolResult.ok(
            "create_primitive",
            {
                "created": True,
                "exists": True,
                "object_name": "Cube",
                "primitive_type": "CUBE",
                "type": "MESH",
                "location": [1.0, 2.0, 3.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "vertex_count": 8,
                "face_count": 6,
            },
        )

        tc = ToolCall(
            call_id="c_create",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [1.0, 2.0, 3.0]},
        )
        self._simulate_tool_call_event(tc, turn_id="turn_create_pass")

        # 1. State returned to PROCESSING and worker delegated next step
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)
        self.mock_worker.submit_task.assert_called_once()

        # 2. Verify tool result
        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertIn("verification", last_res.data)
        verif = last_res.data["verification"]
        self.assertEqual(verif["status"], "PASS")
        self.assertTrue(verif["passed"])
        self.assertEqual(verif["operation"], "create")
        self.assertEqual(verif["target_name"], "Cube")
        self.assertEqual(verif["mismatches"], [])
        self.assertIn("PASSED", verif["summary"])

    def test_low_risk_create_primitive_mismatch_yields_verification_failed(self):
        """When actual scene state does not match expected arguments, runtime transitions to ERROR with VERIFICATION_FAILED."""
        # Expected location is [1.0, 2.0, 3.0], but adapter returns [0.0, 0.0, 0.0]
        self.mock_adapter.create_primitive.return_value = ToolResult.ok(
            "create_primitive",
            {
                "created": True,
                "exists": True,
                "object_name": "Cube",
                "primitive_type": "CUBE",
                "type": "MESH",
                "location": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "vertex_count": 8,
                "face_count": 6,
            },
        )

        tc = ToolCall(
            call_id="c_create_fail",
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "location": [1.0, 2.0, 3.0]},
        )
        res = self._simulate_tool_call_event(tc, turn_id="turn_create_fail")

        # 1. Runtime transitioned to ERROR
        self.assertEqual(self.runtime.current_state, AgentState.ERROR)
        self.assertIsNotNone(res)
        self.assertEqual(res.state, AgentState.ERROR.value)

        # 2. ToolResult is marked failed with VERIFICATION_FAILED
        last_res = self.runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "VERIFICATION_FAILED")
        self.assertIn("location", last_res.error.message)

        # 3. Verification data structured accurately
        self.assertIsNotNone(last_res.data)
        verif = last_res.data["verification"]
        self.assertEqual(verif["status"], "FAIL")
        self.assertFalse(verif["passed"])
        self.assertEqual(len(verif["mismatches"]), 1)
        self.assertEqual(verif["mismatches"][0]["property"], "location")

        # 4. AgentErrorEvent emitted to event_queue
        events = self.event_queue.drain_batch(max_items=50)
        err_events = [e for e in events if isinstance(e, AgentErrorEvent)]
        self.assertEqual(len(err_events), 1)
        self.assertEqual(err_events[0].error_type, "VERIFICATION_FAILED")

    def test_low_risk_transform_auto_executes_and_verifies_pass(self):
        """Absolute transform matches expected vector and succeeds with PASS verification."""
        self.mock_adapter.transform_object.return_value = ToolResult.ok(
            "transform_object",
            {
                "object_name": "Cube",
                "relative": False,
                "exists": True,
                "changed": ["location"],
                "before": {"location": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
                "after": {"exists": True, "location": [5.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
                "actual": {"exists": True, "location": [5.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
            },
        )

        tc = ToolCall(
            call_id="c_trans",
            tool_name="transform_object",
            arguments={"name": "Cube", "location": [5.0, 0.0, 0.0]},
        )
        self._simulate_tool_call_event(tc, turn_id="turn_trans_pass")

        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertEqual(last_res.data["verification"]["status"], "PASS")

    def test_relative_transform_expected_calculated_from_before_and_verified(self):
        """Relative transform calculates expected_after = before + delta and verifies PASS."""
        self.mock_adapter.transform_object.return_value = ToolResult.ok(
            "transform_object",
            {
                "object_name": "Cube",
                "relative": True,
                "exists": True,
                "changed": ["location"],
                "before": {"location": [1.0, 2.0, 3.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
                "after": {"exists": True, "location": [3.0, 2.0, 3.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
                "actual": {"exists": True, "location": [3.0, 2.0, 3.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
            },
        )

        tc = ToolCall(
            call_id="c_rel",
            tool_name="transform_object",
            arguments={"name": "Cube", "location": [2.0, 0.0, 0.0], "relative": True},
        )
        self._simulate_tool_call_event(tc, turn_id="turn_rel_pass")

        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertEqual(last_res.data["verification"]["status"], "PASS")

    def test_medium_risk_delete_approval_flow_and_verification(self):
        """MEDIUM risk delete enters PENDING_APPROVAL; on approve executes and verifies PASS."""
        tc = ToolCall(
            call_id="c_del",
            tool_name="delete_object",
            arguments={"name": "TargetCube"},
        )
        self._simulate_tool_call_event(tc, turn_id="turn_del_gate")

        # 1. Gated: PENDING_APPROVAL, delete not called yet
        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)
        self.mock_adapter.delete_object.assert_not_called()

        # 2. Setup mock adapter return for approve
        self.mock_adapter.delete_object.return_value = ToolResult.ok(
            "delete_object",
            {
                "deleted": True,
                "exists": False,
                "object_name": "TargetCube",
                "type": "MESH",
                "previous_state": {"location": [0.0, 0.0, 0.0]},
            },
        )

        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.approve(approval_id)

        # 3. Executed, verified, and continued
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)
        self.mock_adapter.delete_object.assert_called_once_with(name="TargetCube")
        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertEqual(last_res.data["verification"]["status"], "PASS")
        self.assertEqual(last_res.data["verification"]["operation"], "delete")
        self.assertEqual(last_res.data["verification"]["target_name"], "TargetCube")

    def test_medium_risk_delete_rejection_skips_execution_and_verification(self):
        """Rejecting a pending mutation does not call adapter or verifier."""
        tc = ToolCall(
            call_id="c_del_rej",
            tool_name="delete_object",
            arguments={"name": "ProtectedCube"},
        )
        self._simulate_tool_call_event(tc, turn_id="turn_rej")

        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.reject(approval_id)

        self.mock_adapter.delete_object.assert_not_called()
        last_res = self.runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "USER_REJECTED")
        # No verification occurred
        self.assertIsNone(last_res.data)

    def test_transform_mismatch_yields_verification_failed(self):
        """When transform results do not match expected coordinates, VERIFICATION_FAILED is produced."""
        self.mock_adapter.transform_object.return_value = ToolResult.ok(
            "transform_object",
            {
                "object_name": "Cube",
                "relative": False,
                "exists": True,
                "changed": ["location"],
                "before": {"location": [0.0, 0.0, 0.0]},
                "after": {"exists": True, "location": [0.0, 0.0, 0.0]},
                "actual": {"exists": True, "location": [0.0, 0.0, 0.0]},
            },
        )

        tc = ToolCall(
            call_id="c_trans_fail",
            tool_name="transform_object",
            arguments={"name": "Cube", "location": [10.0, 0.0, 0.0]},
        )
        res = self._simulate_tool_call_event(tc, turn_id="turn_trans_fail")

        self.assertEqual(self.runtime.current_state, AgentState.ERROR)
        last_res = self.runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "VERIFICATION_FAILED")
        self.assertIn("location", last_res.error.message)

    def test_delete_still_exists_yields_verification_failed(self):
        """When object still exists after delete, approve flow produces VERIFICATION_FAILED."""
        tc = ToolCall(
            call_id="c_del_leak",
            tool_name="delete_object",
            arguments={"name": "LeakedCube"},
        )
        self._simulate_tool_call_event(tc, turn_id="turn_del_leak")

        # Mock delete returning exists=True (deletion failed to purge datablock)
        self.mock_adapter.delete_object.return_value = ToolResult.ok(
            "delete_object",
            {
                "deleted": False,
                "exists": True,
                "object_name": "LeakedCube",
                "type": "MESH",
            },
        )

        approval_id = self.runtime.pending_approval.approval_id
        res = self.runtime.approve(approval_id)

        self.assertEqual(self.runtime.current_state, AgentState.ERROR)
        last_res = self.runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "VERIFICATION_FAILED")
        self.assertIn("exists", last_res.error.message)

    def test_read_only_tool_bypasses_verification(self):
        """Read-only inspection tools execute normally and bypass mutation verification."""
        tc = ToolCall(
            call_id="c_inspect",
            tool_name="inspect_scene",
            arguments={},
        )
        self._simulate_tool_call_event(tc, turn_id="turn_ro")

        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertEqual(last_res.data["scene_name"], "TestScene")
        self.assertNotIn("verification", last_res.data)


if __name__ == "__main__":
    unittest.main()
