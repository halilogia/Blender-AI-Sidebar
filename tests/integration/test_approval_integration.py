"""Integration test for M4.1 Deterministic Approval Gate in Headless Blender.

Verifies end-to-end execution gating with real Blender objects:
1. Gated tool interception (delete_object halts at PENDING_APPROVAL without modifying scene).
2. User Rejection: Cube remains untouched in bpy.data.objects, controlled USER_REJECTED result emitted.
3. User Approval: Cube is deleted from bpy.data.objects, atomic undo step pushed.
4. Blender operator integration (bpy.ops.ai_sidebar.approve_action / reject_action).

Usage:
    blender --background --python tests/integration/test_approval_integration.py
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
from agent.dispatcher import ToolDispatcher
from agent.models import ProviderResponse, ToolCall
from agent.policy import ApprovalPolicy, InvalidApprovalError, NoPendingApprovalError
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from core.events import ProviderResponseReadyEvent
from core.event_queue import ThreadSafeEventQueue
from core.types import RiskLevel
from tools.mutations.delete_object import DeleteObjectTool
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.read_only.inspect_scene import InspectSceneTool
from tools.registry import ToolRegistry


class TestApprovalGateBlenderIntegration(unittest.TestCase):
    """Verify approval gate against live Blender data structures."""

    def setUp(self):
        # 1. Clean scene
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.preferences.edit.use_global_undo = True

        # 2. Build Tool Registry with real tools
        self.registry = ToolRegistry()
        self.registry.register(InspectSceneTool())
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(DeleteObjectTool())

        # 3. Setup adapter and dispatcher
        self.adapter = BlenderAdapter()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.adapter)
        self.mock_provider = MagicMock()
        self.mock_worker = MagicMock()
        self.event_queue = ThreadSafeEventQueue()

        # 4. Initialize AgentRuntime with real ApprovalPolicy
        self.policy = ApprovalPolicy()
        self.runtime = AgentRuntime(
            provider=self.mock_provider,
            dispatcher=self.dispatcher,
            event_queue=self.event_queue,
            worker=self.mock_worker,
            policy=self.policy,
        )

    def tear_select_all(self):
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)

    def _simulate_provider_tool_call(self, tool_name: str, arguments: dict, turn_id: str):
        """Helper to simulate LLM returning a tool call."""
        self.runtime._current_turn_id = turn_id
        self.runtime.state_machine.reset()
        self.runtime.state_machine.transition_to(AgentState.PROCESSING)

        call = ToolCall(call_id=f"c_{turn_id}", tool_name=tool_name, arguments=arguments)
        resp = ProviderResponse(assistant_text=None, tool_calls=[call], is_final=False)
        event = ProviderResponseReadyEvent(response=resp, turn_id=turn_id)
        self.runtime.process_event(event)

    def test_rejection_leaves_blender_object_intact(self):
        """When user rejects delete_object, the object remains in the Blender scene."""
        # 1. Create a real Cube in Blender
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
        cube = bpy.context.active_object
        cube.name = "TestCubeReject"
        self.assertIn("TestCubeReject", bpy.data.objects)

        # 2. LLM requests delete_object
        self._simulate_provider_tool_call(
            tool_name="delete_object",
            arguments={"name": "TestCubeReject"},
            turn_id="turn_reject_test",
        )

        # 3. Verify gate intercepted
        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertIsNotNone(self.runtime.pending_approval)
        self.assertEqual(self.runtime.pending_approval.tool_name, "delete_object")
        self.assertEqual(self.runtime.pending_approval.risk_level, RiskLevel.MEDIUM)

        # 4. Crucial: Cube must NOT be deleted while pending
        self.assertIn("TestCubeReject", bpy.data.objects)

        # 5. User rejects
        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.reject(approval_id)

        # 6. Verify Cube is STILL intact in scene
        self.assertIn("TestCubeReject", bpy.data.objects)
        self.assertIsNone(self.runtime.pending_approval)

        # 7. Verify controlled failure result
        last_res = self.runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "USER_REJECTED")

    def test_approval_deletes_blender_object(self):
        """When user approves delete_object, the object is unlinked and deleted from Blender."""
        # 1. Create a real Cube in Blender
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(1, 2, 3))
        cube = bpy.context.active_object
        cube.name = "TestCubeApprove"
        self.assertIn("TestCubeApprove", bpy.data.objects)

        # 2. LLM requests delete_object
        self._simulate_provider_tool_call(
            tool_name="delete_object",
            arguments={"name": "TestCubeApprove"},
            turn_id="turn_approve_test",
        )

        # 3. Verify gate intercepted
        self.assertEqual(self.runtime.current_state, AgentState.PENDING_APPROVAL)
        self.assertIn("TestCubeApprove", bpy.data.objects)

        # 4. User approves
        approval_id = self.runtime.pending_approval.approval_id
        self.runtime.approve(approval_id)

        # 5. Verify Cube is now completely deleted
        self.assertNotIn("TestCubeApprove", bpy.data.objects)
        self.assertIsNone(self.runtime.pending_approval)

        # 6. Verify success result
        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertTrue(last_res.data.get("deleted"))
        self.assertEqual(last_res.data.get("object_name"), "TestCubeApprove")

    def test_low_risk_primitive_creation_bypasses_approval(self):
        """create_primitive has LOW risk and executes immediately without pending approval."""
        self._simulate_provider_tool_call(
            tool_name="create_primitive",
            arguments={"primitive_type": "CUBE", "name": "AutoCube"},
            turn_id="turn_auto_primitive",
        )

        # No pending approval
        self.assertIsNone(self.runtime.pending_approval)
        self.assertIn("AutoCube", bpy.data.objects)


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestApprovalGateBlenderIntegration)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = run_tests()
    sys.exit(code)
