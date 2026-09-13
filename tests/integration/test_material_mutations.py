"""Headless integration tests for M6 Task 1 Material Mutation Tools in Blender 5.2.1.

Verifies live Principled BSDF mutation, slot assignment, thread safety guards,
and native undo/redo restoration.
"""

import sys
import os
import threading

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from adapter.mutators.undo_manager import perform_undo, perform_redo, push_undo_step
from tools.registry import ToolRegistry
from tools.mutations.set_material import SetMaterialTool
from tools.mutations.assign_material import AssignMaterialTool
from core.types import RiskLevel


def clean_scene():
    """Reset scene to a clean default state with undo tracking enabled."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    push_undo_step("Scene Baseline")


def test_tool_registration():
    print("Test 1: Material tools registration and metadata...")
    registry = ToolRegistry()
    s_tool = SetMaterialTool()
    a_tool = AssignMaterialTool()

    registry.register(s_tool)
    registry.register(a_tool)

    assert registry.exists("set_material")
    assert registry.exists("assign_material")
    assert s_tool.risk_level == RiskLevel.LOW
    assert a_tool.risk_level == RiskLevel.LOW
    print("[PASS] Test 1: Material tools registered with RiskLevel.LOW.")


def test_thread_safety_guards():
    print("Test 2: Thread safety enforcement on material mutations...")
    adapter = BlenderAdapter()
    caught_errors = []

    def background_worker():
        try:
            adapter.set_material(object_name="Cube", base_color=[1, 0, 0])
        except ThreadSafetyViolationError:
            caught_errors.append("set_material")

        try:
            adapter.assign_material(object_name="Cube", material_name="Mat")
        except ThreadSafetyViolationError:
            caught_errors.append("assign_material")

    thread = threading.Thread(target=background_worker)
    thread.start()
    thread.join()

    assert caught_errors == ["set_material", "assign_material"], (
        f"Expected 2 ThreadSafetyViolationErrors, got {caught_errors}"
    )
    print("[PASS] Test 2: Thread safety strictly enforced on background thread.")


def test_live_set_material_on_object():
    print("Test 3: Live set_material on mesh object...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create primitive cube
    c_res = adapter.create_primitive("CUBE", name="MaterialCube")
    assert c_res.success

    # Mutate material properties (base_color, metallic, roughness)
    res = adapter.set_material(
        object_name="MaterialCube",
        base_color=[1.0, 0.2, 0.1],  # RGB format -> alpha should default to 1.0
        metallic=0.85,
        roughness=0.15,
        emission_color=[0.0, 1.0, 0.0],
        emission_strength=2.5,
        alpha=0.9,
    )
    assert res.success, f"set_material failed: {res.error}"
    data = res.data
    assert data["material_name"] is not None
    assert "base_color" in data["changed"]
    assert "metallic" in data["changed"]
    assert "roughness" in data["changed"]
    assert "emission_color" in data["changed"]
    assert "emission_strength" in data["changed"]
    assert "alpha" in data["changed"]

    # Verify Blender RNA data
    cube = bpy.data.objects["MaterialCube"]
    assert len(cube.material_slots) >= 1
    mat = cube.material_slots[0].material
    assert mat is not None
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    # Check clamped/assigned values
    base_col = list(bsdf.inputs["Base Color"].default_value)
    assert abs(base_col[0] - 1.0) < 1e-3
    assert abs(base_col[1] - 0.2) < 1e-3
    assert abs(base_col[2] - 0.1) < 1e-3
    assert abs(base_col[3] - 1.0) < 1e-3  # Alpha default
    assert abs(bsdf.inputs["Metallic"].default_value - 0.85) < 1e-3
    assert abs(bsdf.inputs["Roughness"].default_value - 0.15) < 1e-3
    assert abs(bsdf.inputs["Emission Strength"].default_value - 2.5) < 1e-3
    assert abs(bsdf.inputs["Alpha"].default_value - 0.9) < 1e-3

    print("[PASS] Test 3: Live set_material on mesh object verified.")


def test_live_set_material_by_material_name():
    print("Test 4: Live set_material directly on material datablock...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create & configure material directly
    res = adapter.set_material(
        material_name="ChromeMetal",
        base_color=[0.9, 0.9, 0.9, 1.0],
        metallic=1.0,
        roughness=0.05,
    )
    assert res.success, f"set_material failed: {res.error}"
    assert "ChromeMetal" in bpy.data.materials
    mat = bpy.data.materials["ChromeMetal"]
    assert mat.use_nodes is True

    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert abs(bsdf.inputs["Metallic"].default_value - 1.0) < 1e-3
    assert abs(bsdf.inputs["Roughness"].default_value - 0.05) < 1e-3
    print("[PASS] Test 4: Live set_material directly on material datablock verified.")


def test_live_assign_material():
    print("Test 5: Live assign_material to object slots...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive("CUBE", name="TargetCube")
    adapter.set_material(material_name="RedGloss", base_color=[1, 0, 0])

    # Assign RedGloss to TargetCube at slot 0
    res_assign = adapter.assign_material(object_name="TargetCube", material_name="RedGloss", slot_index=0)
    assert res_assign.success, f"assign_material failed: {res_assign.error}"

    cube = bpy.data.objects["TargetCube"]
    assert cube.material_slots[0].material.name == "RedGloss"

    # Assign to slot 1 (slot expansion)
    res_slot1 = adapter.assign_material(object_name="TargetCube", material_name="BlueGloss", slot_index=1)
    assert res_slot1.success, f"assign_material to slot 1 failed: {res_slot1.error}"
    assert len(cube.material_slots) == 2
    assert cube.material_slots[1].material.name == "BlueGloss"
    print("[PASS] Test 5: Live assign_material with slot expansion verified.")


def test_undo_restoration():
    print("Test 6: Undo/redo restoration of material mutation...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive("CUBE", name="UndoCube")
    # Baseline material
    res_init = adapter.set_material(
        object_name="UndoCube",
        base_color=[0.2, 0.2, 0.2, 1.0],
        roughness=0.9,
    )
    assert res_init.success

    cube = bpy.data.objects["UndoCube"]
    mat = cube.material_slots[0].material
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert abs(bsdf.inputs["Roughness"].default_value - 0.9) < 1e-3

    # Mutate to smooth
    res_mut = adapter.set_material(
        object_name="UndoCube",
        roughness=0.1,
    )
    assert res_mut.success
    assert abs(bsdf.inputs["Roughness"].default_value - 0.1) < 1e-3

    # Perform undo
    undo_ok = perform_undo()
    assert undo_ok, "Undo failed to execute."

    # Re-fetch datablock after undo
    cube_after_undo = bpy.data.objects.get("UndoCube")
    mat_after_undo = cube_after_undo.material_slots[0].material
    bsdf_after_undo = next(n for n in mat_after_undo.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert abs(bsdf_after_undo.inputs["Roughness"].default_value - 0.9) < 1e-3, (
        f"Expected roughness 0.9 after undo, got {bsdf_after_undo.inputs['Roughness'].default_value}"
    )

    # Perform redo
    redo_ok = perform_redo()
    assert redo_ok, "Redo failed to execute."
    cube_after_redo = bpy.data.objects.get("UndoCube")
    mat_after_redo = cube_after_redo.material_slots[0].material
    bsdf_after_redo = next(n for n in mat_after_redo.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert abs(bsdf_after_redo.inputs["Roughness"].default_value - 0.1) < 1e-3, (
        f"Expected roughness 0.1 after redo, got {bsdf_after_redo.inputs['Roughness'].default_value}"
    )
    print("[PASS] Test 6: Undo/redo restoration of material mutation verified.")


def run_all():
    print("\n========================================================")
    print("   RUNNING M6 TASK 1 MATERIAL MUTATION INTEGRATION TESTS")
    print("========================================================\n")

    test_tool_registration()
    test_thread_safety_guards()
    test_live_set_material_on_object()
    test_live_set_material_by_material_name()
    test_live_assign_material()
    test_undo_restoration()

    print("\n========================================================")
    print("   ALL M6 TASK 1 MATERIAL MUTATION TESTS PASSED (6/6)    ")
    print("========================================================\n")


if __name__ == "__main__":
    run_all()
