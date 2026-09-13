"""Integration test for M5 Görev 3: Execute -> Verify Runtime in Headless Blender.

Validates end-to-end execution and deterministic verification on live Blender data:
1. CREATE: creates primitive, reads real datablock, verifies PASS with snapshot.
2. TRANSFORM: transforms object, reads real coords, verifies PASS with actual snapshot.
3. DELETE: requires approval, deletes from scene, verifies PASS (exists==False).
4. INTENTIONAL MISMATCH: detects divergence between expected and actual scene, producing VERIFICATION_FAILED.

Usage:
    blender --background --python tests/integration/test_verification_integration.py
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

import bpy

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from adapter.blender_adapter import BlenderAdapter
from adapter.mutators.undo_manager import perform_undo, perform_redo, push_undo_step
from agent.dispatcher import ToolDispatcher
from agent.models import ProviderResponse, ToolCall
from agent.policy import ApprovalPolicy
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from core.events import ProviderResponseReadyEvent
from core.event_queue import ThreadSafeEventQueue
from core.types import RiskLevel, ToolResult
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.delete_object import DeleteObjectTool
from tools.mutations.transform_object import TransformObjectTool
from tools.registry import ToolRegistry


class DivergentAdapter(BlenderAdapter):
    """Adapter subclass that simulates physical/datablock divergence to test failure handling."""

    def transform_object(self, **kwargs):
        res = super().transform_object(**kwargs)
        # Intentionally tamper actual snapshot to simulate undetected scene divergence
        mismatched_data = dict(res.data)
        actual = dict(res.data.get("actual", {}))
        actual["location"] = [-999.0, -999.0, -999.0]
        mismatched_data["actual"] = actual
        return ToolResult.ok(res.tool, mismatched_data)


class TestVerificationBlenderIntegration(unittest.TestCase):
    """Verify runtime verification loop against live Blender engine."""

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.preferences.edit.use_global_undo = True
        push_undo_step("Test Verification Baseline")

        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(TransformObjectTool())
        self.registry.register(DeleteObjectTool())

        self.adapter = BlenderAdapter()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.adapter)
        self.mock_provider = MagicMock()
        self.mock_worker = MagicMock()
        self.event_queue = ThreadSafeEventQueue()
        self.policy = ApprovalPolicy()

        self.runtime = AgentRuntime(
            provider=self.mock_provider,
            dispatcher=self.dispatcher,
            event_queue=self.event_queue,
            worker=self.mock_worker,
            policy=self.policy,
        )

    def _simulate_provider_tool_call(self, tool_name: str, arguments: dict, turn_id: str):
        self.runtime._current_turn_id = turn_id
        self.runtime.state_machine.reset()
        self.runtime.state_machine.transition_to(AgentState.PROCESSING)

        call = ToolCall(call_id=f"c_{turn_id}", tool_name=tool_name, arguments=arguments)
        resp = ProviderResponse(assistant_text=None, tool_calls=[call], is_final=False)
        event = ProviderResponseReadyEvent(response=resp, turn_id=turn_id)
        return self.runtime.process_event(event)

    def test_create_primitive_verifies_pass(self):
        """create_primitive executes, verifies live datablock, and attaches PASS verification."""
        self._simulate_provider_tool_call(
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "name": "VerifiedCube", "location": [1.0, 2.0, 3.0]},
            turn_id="turn_v_create",
        )

        self.assertIn("VerifiedCube", bpy.data.objects)
        obj = bpy.data.objects["VerifiedCube"]
        self.assertEqual(list(obj.location), [1.0, 2.0, 3.0])

        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertIn("verification", last_res.data)
        verif = last_res.data["verification"]
        self.assertEqual(verif["status"], "PASS")
        self.assertTrue(verif["passed"])
        self.assertEqual(verif["operation"], "create")
        self.assertEqual(verif["target_name"], "VerifiedCube")

    def test_transform_object_verifies_pass(self):
        """transform_object modifies live object and verifies PASS with live snapshot."""
        # 1. Create base object
        self.adapter.create_primitive("CUBE", name="TransCube", location=[0.0, 0.0, 0.0])
        self.assertIn("TransCube", bpy.data.objects)

        # 2. Transform via runtime
        self._simulate_provider_tool_call(
            tool_name="transform_object",
            arguments={"name": "TransCube", "location": [3.0, 4.0, 5.0]},
            turn_id="turn_v_trans",
        )

        obj = bpy.data.objects["TransCube"]
        self.assertEqual(list(obj.location), [3.0, 4.0, 5.0])

        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        verif = last_res.data["verification"]
        self.assertEqual(verif["status"], "PASS")
        self.assertTrue(verif["passed"])
        self.assertEqual(verif["operation"], "transform")

    def test_delete_object_approval_and_verifies_pass(self):
        """delete_object halts at approval, deletes on approve, and verifies absence (exists==False)."""
        # 1. Create target
        self.adapter.create_primitive("CUBE", name="DelCube", location=[0.0, 0.0, 0.0])
        self.assertIn("DelCube", bpy.data.objects)

        # 2. Request deletion
        self._simulate_provider_tool_call(
            tool_name="delete_object",
            arguments={"name": "DelCube"},
            turn_id="turn_v_del",
        )

        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertIn("DelCube", bpy.data.objects)

        # 3. Approve deletion
        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.approve(approval_id)

        self.assertNotIn("DelCube", bpy.data.objects)
        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertTrue(last_res.data["deleted"])
        self.assertFalse(last_res.data["exists"])
        verif = last_res.data["verification"]
        self.assertEqual(verif["status"], "PASS")
        self.assertTrue(verif["passed"])
        self.assertEqual(verif["operation"], "delete")

    def test_intentional_mismatch_yields_verification_failed(self):
        """When actual scene diverges from expected target, runtime halts with VERIFICATION_FAILED."""
        # 1. Create object with standard adapter
        self.adapter.create_primitive("CUBE", name="DivergeCube", location=[0.0, 0.0, 0.0])

        # 2. Setup runtime with DivergentAdapter
        divergent_adapter = DivergentAdapter()
        divergent_dispatcher = ToolDispatcher(registry=self.registry, adapter=divergent_adapter)
        divergent_runtime = AgentRuntime(
            provider=self.mock_provider,
            dispatcher=divergent_dispatcher,
            policy=self.policy,
        )

        # 3. Request transform
        divergent_runtime._current_turn_id = "turn_diverge"
        divergent_runtime.state_machine.reset()
        divergent_runtime.state_machine.transition_to(AgentState.PROCESSING)
        call = ToolCall(call_id="c_div", tool_name="transform_object", arguments={"name": "DivergeCube", "location": [5.0, 5.0, 5.0]})
        resp = ProviderResponse(assistant_text=None, tool_calls=[call], is_final=False)
        event = ProviderResponseReadyEvent(response=resp, turn_id="turn_diverge")

        result = divergent_runtime.process_event(event)

        # 4. State must be ERROR, result must be VERIFICATION_FAILED
        self.assertEqual(divergent_runtime.current_state, AgentState.ERROR)
        self.assertIsNotNone(result)
        self.assertEqual(result.state, AgentState.ERROR.value)

        last_res = divergent_runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "VERIFICATION_FAILED")
        self.assertIn("location", last_res.error.message)
        self.assertFalse(last_res.data["verification"]["passed"])

    def test_delete_object_rejection_prevents_mutation_and_verification(self):
        """When user rejects delete_object, scene object remains intact and verifier is NOT invoked."""
        # 1. Create target
        self.adapter.create_primitive("CUBE", name="RejectMe", location=[0.0, 0.0, 0.0])
        self.assertIn("RejectMe", bpy.data.objects)

        # 2. Request delete
        self._simulate_provider_tool_call(
            tool_name="delete_object",
            arguments={"name": "RejectMe"},
            turn_id="turn_v_reject",
        )

        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertIn("RejectMe", bpy.data.objects)

        # 3. Reject
        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.reject(approval_id)

        # 4. Verify object is intact, result is USER_REJECTED, and verification was bypassed
        self.assertIn("RejectMe", bpy.data.objects)
        last_res = self.runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "USER_REJECTED")
        self.assertIsNone(last_res.data)
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

    def test_verification_preserves_undo_and_timing(self):
        """Verified mutation creates atomic undo step; undo and redo work without interference."""
        # 1. Create primitive via runtime
        self._simulate_provider_tool_call(
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "name": "UndoCube", "location": [1.0, 1.0, 1.0]},
            turn_id="turn_v_undo",
        )

        self.assertIn("UndoCube", bpy.data.objects)
        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertEqual(last_res.data["verification"]["status"], "PASS")

        # 2. Undo operation in Blender
        self.assertTrue(perform_undo(), "perform_undo failed")
        self.assertNotIn("UndoCube", bpy.data.objects)

        # 3. Redo operation in Blender
        self.assertTrue(perform_redo(), "perform_redo failed")
        self.assertIn("UndoCube", bpy.data.objects)


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestVerificationBlenderIntegration)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = run_tests()
    sys.exit(code)
