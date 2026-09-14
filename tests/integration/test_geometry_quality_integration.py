"""Integration tests for M9 Task 5: Core Geometry Quality in headless Blender.

Verifies live polygon shading (SMOOTH/FLAT) and geometry modifiers (BEVEL, SUBSURF, BOOLEAN),
parameter validation, thread safety guards, undo restoration, and M5 ChangeVerifier integration.
"""

import sys
import threading
import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from adapter.mutators.undo_manager import perform_undo, perform_redo, push_undo_step
from agent.verifier import ChangeVerifier, build_change_set_from_result
from tools.registry import ToolRegistry
from tools.mutations.set_shading import SetShadingTool
from tools.mutations.add_modifier import AddModifierTool


def clean_scene():
    """Reset scene to a completely empty state and initialize baseline undo point."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    push_undo_step("Initial Scene Baseline")


def test_tool_registration():
    print("Test 1: Tool registration and dynamic risk metadata...")
    registry = ToolRegistry()
    shading_tool = SetShadingTool()
    modifier_tool = AddModifierTool()

    registry.register(shading_tool)
    registry.register(modifier_tool)

    assert registry.exists("set_shading")
    assert registry.exists("add_modifier")

    assert shading_tool.risk_level.value == "LOW"
    assert modifier_tool.get_risk_level({"modifier_type": "BEVEL"}).value == "LOW"
    assert modifier_tool.get_risk_level({"modifier_type": "SUBSURF"}).value == "LOW"
    assert modifier_tool.get_risk_level({"modifier_type": "BOOLEAN"}).value == "MEDIUM"

    print("[PASS] Test 1: Tool registration and dynamic risk metadata verified.")


def test_thread_safety_guards():
    print("Test 2: Thread safety guards...")
    adapter = BlenderAdapter()
    caught_errors = []

    def background_worker():
        try:
            adapter.set_shading(name="NonExistent", shading="SMOOTH")
        except ThreadSafetyViolationError:
            caught_errors.append("set_shading")

        try:
            adapter.add_modifier(name="NonExistent", modifier_type="BEVEL")
        except ThreadSafetyViolationError:
            caught_errors.append("add_modifier")

    thread = threading.Thread(target=background_worker)
    thread.start()
    thread.join()

    assert caught_errors == ["set_shading", "add_modifier"], (
        f"Expected 2 ThreadSafetyViolationError, got {caught_errors}"
    )
    print("[PASS] Test 2: ThreadSafetyViolationError strictly enforced on background thread.")


def test_set_shading_smooth_and_flat():
    print("Test 3: Live set_shading (SMOOTH and FLAT)...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create Sphere
    res_sphere = adapter.create_primitive("SPHERE", name="ShadingSphere")
    assert res_sphere.success
    sphere_obj = bpy.data.objects["ShadingSphere"]

    # 1. Apply SMOOTH
    res_smooth = adapter.set_shading(name="ShadingSphere", shading="SMOOTH")
    assert res_smooth.success, f"Smooth shading failed: {res_smooth.error}"
    assert all(p.use_smooth for p in sphere_obj.data.polygons), "All polygons must have use_smooth=True"
    assert res_smooth.data["shading"] == "SMOOTH"

    # Verification integration for SMOOTH
    verifier = ChangeVerifier()
    cs_smooth = build_change_set_from_result("set_shading", {"name": "ShadingSphere", "shading": "SMOOTH"}, res_smooth.data)
    assert cs_smooth is not None
    assert verifier.verify(cs_smooth).passed is True

    # 2. Apply FLAT
    res_flat = adapter.set_shading(name="ShadingSphere", shading="FLAT")
    assert res_flat.success
    assert not any(p.use_smooth for p in sphere_obj.data.polygons), "All polygons must have use_smooth=False"
    assert res_flat.data["shading"] == "FLAT"

    # 3. Fail-closed on invalid shading
    res_inv = adapter.set_shading(name="ShadingSphere", shading="INVALID_MODE")
    assert not res_inv.success
    assert "Must be one of" in res_inv.error.message

    # 4. Fail-closed on non-mesh object (create camera and try shading)
    res_cam = adapter.create_camera(name="NonMeshObj")
    assert res_cam.success
    res_non_mesh = adapter.set_shading(name="NonMeshObj", shading="SMOOTH")
    assert not res_non_mesh.success
    assert "Only MESH objects support polygon shading" in res_non_mesh.error.message

    print("[PASS] Test 3: Live set_shading (SMOOTH, FLAT, error cases) verified.")


def test_add_bevel_modifier():
    print("Test 4: Live BEVEL modifier...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive("CUBE", name="BevelCube")
    cube_obj = bpy.data.objects["BevelCube"]

    res_bevel = adapter.add_modifier(
        name="BevelCube",
        modifier_type="BEVEL",
        width=0.04,
        segments=3,
    )
    assert res_bevel.success, f"Bevel failed: {res_bevel.error}"
    assert "Bevel" in cube_obj.modifiers
    mod = cube_obj.modifiers["Bevel"]
    assert mod.type == "BEVEL"
    assert abs(mod.width - 0.04) < 1e-4
    assert mod.segments == 3

    # Verification
    verifier = ChangeVerifier()
    args = {"name": "BevelCube", "modifier_type": "BEVEL", "width": 0.04, "segments": 3}
    cs = build_change_set_from_result("add_modifier", args, res_bevel.data)
    assert cs is not None
    assert verifier.verify(cs).passed is True

    print("[PASS] Test 4: Live BEVEL modifier verified.")


def test_add_subsurf_modifier():
    print("Test 5: Live SUBSURF modifier...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive("CUBE", name="SubsurfCube")
    cube_obj = bpy.data.objects["SubsurfCube"]

    res_subsurf = adapter.add_modifier(
        name="SubsurfCube",
        modifier_type="SUBSURF",
        levels=2,
    )
    assert res_subsurf.success, f"Subsurf failed: {res_subsurf.error}"
    assert "Subsurf" in cube_obj.modifiers
    mod = cube_obj.modifiers["Subsurf"]
    assert mod.type == "SUBSURF"
    assert mod.levels == 2

    # Verification
    verifier = ChangeVerifier()
    args = {"name": "SubsurfCube", "modifier_type": "SUBSURF", "levels": 2}
    cs = build_change_set_from_result("add_modifier", args, res_subsurf.data)
    assert cs is not None
    assert verifier.verify(cs).passed is True

    print("[PASS] Test 5: Live SUBSURF modifier verified.")


def test_add_boolean_modifier():
    print("Test 6: Live BOOLEAN modifier (DIFFERENCE and UNION)...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create Wall and Cutter
    res_w = adapter.create_primitive("CUBE", name="Wall")
    assert res_w.success
    res_c = adapter.create_primitive("SPHERE", name="Cutter", location=[0.0, 0.0, 0.0])
    assert res_c.success
    wall_obj = bpy.data.objects["Wall"]

    # 1. DIFFERENCE
    res_diff = adapter.add_modifier(
        name="Wall",
        modifier_type="BOOLEAN",
        operation="DIFFERENCE",
        target_object="Cutter",
    )
    assert res_diff.success, f"Boolean DIFFERENCE failed: {res_diff.error}"
    assert "Boolean" in wall_obj.modifiers
    mod = wall_obj.modifiers["Boolean"]
    assert mod.type == "BOOLEAN"
    assert mod.operation == "DIFFERENCE"
    assert mod.object.name == "Cutter"

    # Verification
    verifier = ChangeVerifier()
    args_diff = {"name": "Wall", "modifier_type": "BOOLEAN", "operation": "DIFFERENCE", "target_object": "Cutter"}
    cs_diff = build_change_set_from_result("add_modifier", args_diff, res_diff.data)
    assert cs_diff is not None
    assert verifier.verify(cs_diff).passed is True

    # 2. UNION (update existing or add)
    res_union = adapter.add_modifier(
        name="Wall",
        modifier_type="BOOLEAN",
        modifier_name="BoolUnion",
        operation="UNION",
        target_object="Cutter",
    )
    assert res_union.success
    mod_u = wall_obj.modifiers["BoolUnion"]
    assert mod_u.operation == "UNION"

    # 3. Fail-closed: missing target_object
    res_missing_tgt = adapter.add_modifier(name="Wall", modifier_type="BOOLEAN")
    assert not res_missing_tgt.success
    assert "requires a valid 'target_object'" in res_missing_tgt.error.message

    # 4. Fail-closed: same object as target
    res_self_tgt = adapter.add_modifier(name="Wall", modifier_type="BOOLEAN", target_object="Wall")
    assert not res_self_tgt.success
    assert "cannot use the same object as its target" in res_self_tgt.error.message

    # 5. Fail-closed: non-existent target
    res_ghost_tgt = adapter.add_modifier(name="Wall", modifier_type="BOOLEAN", target_object="Ghost")
    assert not res_ghost_tgt.success
    assert "not found in scene" in res_ghost_tgt.error.message

    print("[PASS] Test 6: Live BOOLEAN modifier and error handling verified.")


def test_modifier_undo_restoration():
    print("Test 7: Modifier undo restoration...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive("CUBE", name="UndoCube")
    cube_obj = bpy.data.objects["UndoCube"]

    # Add modifier
    res = adapter.add_modifier(name="UndoCube", modifier_type="BEVEL")
    assert res.success
    assert len(cube_obj.modifiers) == 1

    # Undo
    perform_undo()
    cube_after_undo = bpy.data.objects.get("UndoCube")
    assert cube_after_undo is not None
    assert len(cube_after_undo.modifiers) == 0, "Undo must remove added modifier"

    # Redo
    perform_redo()
    cube_after_redo = bpy.data.objects.get("UndoCube")
    assert cube_after_redo is not None
    assert len(cube_after_redo.modifiers) == 1, "Redo must restore modifier"

    print("[PASS] Test 7: Modifier undo restoration verified.")


def main():
    print("\n========================================================")
    print("   RUNNING GEOMETRY QUALITY INTEGRATION TESTS           ")
    print("========================================================\n")

    test_tool_registration()
    test_thread_safety_guards()
    test_set_shading_smooth_and_flat()
    test_add_bevel_modifier()
    test_add_subsurf_modifier()
    test_add_boolean_modifier()
    test_modifier_undo_restoration()

    print("\n========================================================")
    print("   ALL GEOMETRY QUALITY INTEGRATION TESTS PASSED (7/7)  ")
    print("========================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
