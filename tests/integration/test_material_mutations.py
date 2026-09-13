"""Headless integration and acceptance suite for M6 Materials & Shader Tools in Blender 5.2.1.

Validates end-to-end material mutation and verification against live Blender engine:
1. set_material real tool flow via ToolRegistry & ToolDispatcher on live mesh.
2. assign_material real tool flow with slot binding.
3. Live verification PASS via AgentRuntime / ChangeVerifier.
4. Live verification FAIL: intentional divergence triggers VERIFICATION_FAILED.
5. Partial verification: changing only roughness ignores untouched fields.
6. RGB normalization: 3-element RGB input maps to 4-element RGBA in Blender RNA.
7. Clamping: negative and >1 inputs clamped to [0, 1] in live Blender.
8. Slot expansion: slot_index=2 automatically expands material slots.
9. Undo/Redo: native Blender undo/redo state restoration.
10. Thread safety: background thread calls raise ThreadSafetyViolationError.
"""

import os
import sys
import threading
import unittest
from unittest.mock import MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from adapter.mutators.undo_manager import perform_undo, perform_redo, push_undo_step
from agent.dispatcher import ToolDispatcher
from agent.models import ProviderResponse, ToolCall
from agent.policy import ApprovalPolicy
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from agent.verifier import ChangeVerifier
from core.events import ProviderResponseReadyEvent
from core.event_queue import ThreadSafeEventQueue
from core.types import RiskLevel, ToolResult
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.set_material import SetMaterialTool
from tools.mutations.assign_material import AssignMaterialTool
from tools.registry import ToolRegistry


class DivergentMaterialAdapter(BlenderAdapter):
    """Adapter that simulates intentional shader divergence to test verification failure handling."""

    def set_material(self, **kwargs):
        res = super().set_material(**kwargs)
        if not res.success:
            return res
        # Intentionally tamper actual snapshot to simulate undetected scene divergence
        mismatched_data = dict(res.data)
        actual = dict(res.data.get("actual", {}))
        bsdf = dict(actual.get("principled_bsdf", {}))
        # Modify roughness to a diverged value that exceeds epsilon
        bsdf["roughness"] = 0.999
        actual["principled_bsdf"] = bsdf
        mismatched_data["actual"] = actual
        return ToolResult.ok(res.tool, mismatched_data)


class TestMaterialIntegrationSuite(unittest.TestCase):
    """End-to-end acceptance and integration tests for M6 Materials."""

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.preferences.edit.use_global_undo = True
        push_undo_step("Scene Baseline")

        self.registry = ToolRegistry()
        self.registry.register(CreatePrimitiveTool())
        self.registry.register(SetMaterialTool())
        self.registry.register(AssignMaterialTool())

        self.adapter = BlenderAdapter()
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=self.adapter)
        self.mock_provider = MagicMock()
        self.mock_worker = MagicMock()
        self.event_queue = ThreadSafeEventQueue()
        self.policy = ApprovalPolicy()
        self.verifier = ChangeVerifier(epsilon=1e-3)

        self.runtime = AgentRuntime(
            provider=self.mock_provider,
            dispatcher=self.dispatcher,
            event_queue=self.event_queue,
            worker=self.mock_worker,
            policy=self.policy,
            verifier=self.verifier,
        )

    def _simulate_provider_tool_call(self, tool_name: str, arguments: dict, turn_id: str):
        self.runtime._current_turn_id = turn_id
        self.runtime.state_machine.reset()
        self.runtime.state_machine.transition_to(AgentState.PROCESSING)

        call = ToolCall(call_id=f"c_{turn_id}", tool_name=tool_name, arguments=arguments)
        resp = ProviderResponse(assistant_text=None, tool_calls=[call], is_final=False)
        event = ProviderResponseReadyEvent(response=resp, turn_id=turn_id)
        return self.runtime.process_event(event)

    # -------------------------------------------------------------------------
    # 1. set_material real tool flow via Registry & Dispatcher
    # -------------------------------------------------------------------------
    def test_01_set_material_real_tool_flow(self):
        """set_material dispatches through ToolDispatcher and modifies all Principled BSDF fields on live mesh."""
        self.adapter.create_primitive("CUBE", name="FlowCube")

        tc = ToolCall(
            call_id="call_flow_1",
            tool_name="set_material",
            arguments={
                "object_name": "FlowCube",
                "base_color": [0.8, 0.1, 0.2, 1.0],
                "metallic": 0.9,
                "roughness": 0.15,
                "emission_color": [0.1, 0.9, 0.2, 1.0],
                "emission_strength": 4.0,
                "alpha": 0.85,
            },
        )
        res = self.dispatcher.dispatch(tc)
        self.assertTrue(res.success, f"Dispatcher failed: {res.error}")

        # Verify live Blender RNA datablocks
        cube = bpy.data.objects["FlowCube"]
        self.assertGreaterEqual(len(cube.material_slots), 1)
        mat = cube.material_slots[0].material
        self.assertIsNotNone(mat)
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

        # Check values
        base_col = list(bsdf.inputs["Base Color"].default_value)
        self.assertAlmostEqual(base_col[0], 0.8, places=3)
        self.assertAlmostEqual(base_col[1], 0.1, places=3)
        self.assertAlmostEqual(base_col[2], 0.2, places=3)
        self.assertAlmostEqual(base_col[3], 1.0, places=3)
        self.assertAlmostEqual(bsdf.inputs["Metallic"].default_value, 0.9, places=3)
        self.assertAlmostEqual(bsdf.inputs["Roughness"].default_value, 0.15, places=3)
        self.assertAlmostEqual(bsdf.inputs["Emission Strength"].default_value, 4.0, places=3)
        self.assertAlmostEqual(bsdf.inputs["Alpha"].default_value, 0.85, places=3)

    # -------------------------------------------------------------------------
    # 2. assign_material real tool flow
    # -------------------------------------------------------------------------
    def test_02_assign_material_real_tool_flow(self):
        """assign_material dispatches through ToolDispatcher and binds slots 0 and 1 correctly."""
        self.adapter.create_primitive("CUBE", name="AssignCube")

        # Slot 0 assignment
        tc0 = ToolCall(
            call_id="call_assign_0",
            tool_name="assign_material",
            arguments={"object_name": "AssignCube", "material_name": "MatSlot0", "slot_index": 0},
        )
        res0 = self.dispatcher.dispatch(tc0)
        self.assertTrue(res0.success)

        # Slot 1 assignment
        tc1 = ToolCall(
            call_id="call_assign_1",
            tool_name="assign_material",
            arguments={"object_name": "AssignCube", "material_name": "MatSlot1", "slot_index": 1},
        )
        res1 = self.dispatcher.dispatch(tc1)
        self.assertTrue(res1.success)

        # Verify Blender RNA bindings
        cube = bpy.data.objects["AssignCube"]
        self.assertEqual(len(cube.material_slots), 2)
        self.assertEqual(cube.material_slots[0].material.name, "MatSlot0")
        self.assertEqual(cube.material_slots[1].material.name, "MatSlot1")

    # -------------------------------------------------------------------------
    # 3. Real verification PASS
    # -------------------------------------------------------------------------
    def test_03_real_verification_pass(self):
        """set_material executed through AgentRuntime verifies PASS with attached verification result."""
        self.adapter.create_primitive("CUBE", name="PassCube")

        self._simulate_provider_tool_call(
            tool_name="set_material",
            arguments={
                "object_name": "PassCube",
                "base_color": [0.2, 0.4, 0.6, 1.0],
                "metallic": 0.7,
                "roughness": 0.3,
            },
            turn_id="turn_v_pass",
        )

        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        self.assertIn("verification", last_res.data)
        verif = last_res.data["verification"]
        self.assertEqual(verif["status"], "PASS")
        self.assertTrue(verif["passed"])
        self.assertEqual(verif["operation"], "set_material")
        self.assertEqual(len(verif["mismatches"]), 0)

    # -------------------------------------------------------------------------
    # 4. Real verification FAIL (Intentional divergence detection)
    # -------------------------------------------------------------------------
    def test_04_real_verification_fail(self):
        """Intentional mismatch between expected and actual state produces VERIFICATION_FAILED."""
        self.adapter.create_primitive("CUBE", name="DivergentCube")

        # Set up runtime with DivergentMaterialAdapter
        divergent_adapter = DivergentMaterialAdapter()
        divergent_dispatcher = ToolDispatcher(registry=self.registry, adapter=divergent_adapter)
        divergent_runtime = AgentRuntime(
            provider=self.mock_provider,
            dispatcher=divergent_dispatcher,
            event_queue=ThreadSafeEventQueue(),
            worker=self.mock_worker,
            policy=self.policy,
            verifier=self.verifier,
        )

        # Execute set_material expecting roughness=0.1
        divergent_runtime._current_turn_id = "turn_v_fail"
        divergent_runtime.state_machine.reset()
        divergent_runtime.state_machine.transition_to(AgentState.PROCESSING)

        call = ToolCall(
            call_id="call_fail_1",
            tool_name="set_material",
            arguments={"object_name": "DivergentCube", "roughness": 0.1},
        )
        resp = ProviderResponse(assistant_text=None, tool_calls=[call], is_final=False)
        event = ProviderResponseReadyEvent(response=resp, turn_id="turn_v_fail")
        divergent_runtime.process_event(event)

        last_res = divergent_runtime._current_tool_results[-1]
        self.assertFalse(last_res.success)
        self.assertEqual(last_res.error.type, "VERIFICATION_FAILED")
        self.assertIn("mismatch in [roughness]", last_res.error.message)
        self.assertIn("verification", last_res.error.details)
        verif = last_res.error.details["verification"]
        self.assertEqual(verif["status"], "FAIL")
        self.assertFalse(verif["passed"])
        self.assertEqual(verif["mismatches"][0]["property"], "roughness")

    # -------------------------------------------------------------------------
    # 5. Partial verification
    # -------------------------------------------------------------------------
    def test_05_partial_verification_ignores_untouched_fields(self):
        """Mutating only roughness verifies PASS without falsely failing on other default shader properties."""
        self.adapter.create_primitive("CUBE", name="PartialCube")

        self._simulate_provider_tool_call(
            tool_name="set_material",
            arguments={"object_name": "PartialCube", "roughness": 0.42},
            turn_id="turn_v_partial",
        )

        last_res = self.runtime._current_tool_results[-1]
        self.assertTrue(last_res.success)
        verif = last_res.data["verification"]
        self.assertEqual(verif["status"], "PASS")

    # -------------------------------------------------------------------------
    # 6. RGB normalization (3-element RGB -> 4-element RGBA)
    # -------------------------------------------------------------------------
    def test_06_rgb_normalization_to_rgba(self):
        """3-element RGB input produces 4-element RGBA with alpha=1.0 in live Blender RNA."""
        self.adapter.create_primitive("CUBE", name="RgbCube")

        tc = ToolCall(
            call_id="call_rgb_1",
            tool_name="set_material",
            arguments={
                "object_name": "RgbCube",
                "base_color": [0.3, 0.6, 0.9],
                "emission_color": [1.0, 0.5, 0.0],
            },
        )
        res = self.dispatcher.dispatch(tc)
        self.assertTrue(res.success)

        mat = bpy.data.objects["RgbCube"].material_slots[0].material
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

        base_val = list(bsdf.inputs["Base Color"].default_value)
        self.assertEqual(len(base_val), 4)
        self.assertAlmostEqual(base_val[0], 0.3, places=3)
        self.assertAlmostEqual(base_val[1], 0.6, places=3)
        self.assertAlmostEqual(base_val[2], 0.9, places=3)
        self.assertAlmostEqual(base_val[3], 1.0, places=3)

        em_val = list(bsdf.inputs["Emission Color"].default_value)
        self.assertEqual(len(em_val), 4)
        self.assertAlmostEqual(em_val[0], 1.0, places=3)
        self.assertAlmostEqual(em_val[1], 0.5, places=3)
        self.assertAlmostEqual(em_val[2], 0.0, places=3)
        self.assertAlmostEqual(em_val[3], 1.0, places=3)

    # -------------------------------------------------------------------------
    # 7. Clamping of out-of-range inputs
    # -------------------------------------------------------------------------
    def test_07_clamping_out_of_range_values(self):
        """Inputs outside [0, 1] are clamped safely in Blender RNA."""
        self.adapter.create_primitive("CUBE", name="ClampCube")

        tc = ToolCall(
            call_id="call_clamp_1",
            tool_name="set_material",
            arguments={
                "object_name": "ClampCube",
                "base_color": [-0.5, 1.5, 0.5],
                "metallic": -0.2,
                "roughness": 1.8,
                "alpha": 2.0,
                "emission_strength": -5.0,
            },
        )
        res = self.dispatcher.dispatch(tc)
        self.assertTrue(res.success)

        mat = bpy.data.objects["ClampCube"].material_slots[0].material
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

        base_val = list(bsdf.inputs["Base Color"].default_value)
        self.assertAlmostEqual(base_val[0], 0.0, places=3)  # Clamped from -0.5
        self.assertAlmostEqual(base_val[1], 1.0, places=3)  # Clamped from 1.5
        self.assertAlmostEqual(base_val[2], 0.5, places=3)

        self.assertAlmostEqual(bsdf.inputs["Metallic"].default_value, 0.0, places=3)  # Clamped from -0.2
        self.assertAlmostEqual(bsdf.inputs["Roughness"].default_value, 1.0, places=3)  # Clamped from 1.8
        self.assertAlmostEqual(bsdf.inputs["Alpha"].default_value, 1.0, places=3)  # Clamped from 2.0
        self.assertAlmostEqual(bsdf.inputs["Emission Strength"].default_value, 0.0, places=3)  # Clamped from -5.0

    # -------------------------------------------------------------------------
    # 8. Slot expansion
    # -------------------------------------------------------------------------
    def test_08_slot_expansion(self):
        """assign_material with slot_index=2 expands empty slots and binds material at index 2."""
        self.adapter.create_primitive("CUBE", name="ExpandCube")

        tc = ToolCall(
            call_id="call_expand_1",
            tool_name="assign_material",
            arguments={"object_name": "ExpandCube", "material_name": "Slot2Mat", "slot_index": 2},
        )
        res = self.dispatcher.dispatch(tc)
        self.assertTrue(res.success)

        cube = bpy.data.objects["ExpandCube"]
        self.assertEqual(len(cube.material_slots), 3)
        self.assertIsNone(cube.material_slots[1].material)
        self.assertEqual(cube.material_slots[2].material.name, "Slot2Mat")

    # -------------------------------------------------------------------------
    # 9. Undo/Redo restoration
    # -------------------------------------------------------------------------
    def test_09_undo_redo_restoration(self):
        """Native Blender Ctrl+Z undo and redo restore exact material state."""
        self.adapter.create_primitive("CUBE", name="UndoCube")

        # 1. Baseline roughness = 0.9
        self.adapter.set_material(object_name="UndoCube", roughness=0.9)
        cube = bpy.data.objects["UndoCube"]
        mat = cube.material_slots[0].material
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertAlmostEqual(bsdf.inputs["Roughness"].default_value, 0.9, places=3)

        # 2. Mutate to roughness = 0.1
        self.adapter.set_material(object_name="UndoCube", roughness=0.1)
        self.assertAlmostEqual(bsdf.inputs["Roughness"].default_value, 0.1, places=3)

        # 3. Undo -> should restore roughness = 0.9
        self.assertTrue(perform_undo(), "Undo failed to execute")
        cube_after_undo = bpy.data.objects.get("UndoCube")
        mat_after_undo = cube_after_undo.material_slots[0].material
        bsdf_after_undo = next(n for n in mat_after_undo.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertAlmostEqual(bsdf_after_undo.inputs["Roughness"].default_value, 0.9, places=3)

        # 4. Redo -> should restore roughness = 0.1
        self.assertTrue(perform_redo(), "Redo failed to execute")
        cube_after_redo = bpy.data.objects.get("UndoCube")
        mat_after_redo = cube_after_redo.material_slots[0].material
        bsdf_after_redo = next(n for n in mat_after_redo.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertAlmostEqual(bsdf_after_redo.inputs["Roughness"].default_value, 0.1, places=3)

    # -------------------------------------------------------------------------
    # 10. Thread safety enforcement
    # -------------------------------------------------------------------------
    def test_10_thread_safety_guards(self):
        """Calling set_material or assign_material from background thread raises ThreadSafetyViolationError."""
        caught_errors = []

        def background_worker():
            try:
                self.adapter.set_material(object_name="Cube", base_color=[1, 0, 0])
            except ThreadSafetyViolationError:
                caught_errors.append("set_material")

            try:
                self.adapter.assign_material(object_name="Cube", material_name="Mat")
            except ThreadSafetyViolationError:
                caught_errors.append("assign_material")

        thread = threading.Thread(target=background_worker)
        thread.start()
        thread.join()

        self.assertEqual(caught_errors, ["set_material", "assign_material"])


def run_tests():
    print("\n========================================================")
    print("   RUNNING M6 TASK 3 MATERIAL INTEGRATION SUITE         ")
    print("========================================================\n")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMaterialIntegrationSuite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = run_tests()
    sys.exit(code)
