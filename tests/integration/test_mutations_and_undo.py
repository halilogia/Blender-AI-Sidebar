"""Integration tests for M3.1 Safe Mutation & Undo Foundation in Blender 5.2.1.

Verifies live creation, transformation, deletion, thread safety guards, and
strict native Blender undo/redo state restoration.
"""

import sys
import threading
import bpy
from mathutils import Euler, Vector

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from adapter.mutators.undo_manager import perform_undo, perform_redo
from tools.registry import ToolRegistry
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.transform_object import TransformObjectTool
from tools.mutations.delete_object import DeleteObjectTool


def clean_scene():
    """Reset scene to a completely empty state and initialize baseline undo point."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    # In background mode, Blender disables undo until explicitly initialized
    from adapter.mutators.undo_manager import push_undo_step
    push_undo_step("Initial Scene Baseline")


def test_tool_registration():
    print("Test 1: Tool registration and metadata...")
    registry = ToolRegistry()
    c_tool = CreatePrimitiveTool()
    t_tool = TransformObjectTool()
    d_tool = DeleteObjectTool()

    registry.register(c_tool)
    registry.register(t_tool)
    registry.register(d_tool)

    assert registry.exists("create_primitive")
    assert registry.exists("transform_object")
    assert registry.exists("delete_object")
    assert len(registry) == 3
    print("[PASS] Test 1: Tool registration and metadata verified.")


def test_thread_safety_guards():
    print("Test 2: Thread safety guards on mutation methods...")
    adapter = BlenderAdapter()
    caught_errors = []

    def background_worker():
        try:
            adapter.create_primitive("CUBE")
        except ThreadSafetyViolationError:
            caught_errors.append("create_primitive")

        try:
            adapter.transform_object("AnyObject", location=[0, 0, 1])
        except ThreadSafetyViolationError:
            caught_errors.append("transform_object")

        try:
            adapter.delete_object("AnyObject")
        except ThreadSafetyViolationError:
            caught_errors.append("delete_object")

    thread = threading.Thread(target=background_worker)
    thread.start()
    thread.join()

    assert caught_errors == ["create_primitive", "transform_object", "delete_object"], (
        f"Expected 3 ThreadSafetyViolationError, got {caught_errors}"
    )
    print("[PASS] Test 2: ThreadSafetyViolationError strictly enforced on background thread.")


def test_create_primitive():
    print("Test 3: Live primitive creation (CUBE, SPHERE, PLANE)...")
    clean_scene()
    adapter = BlenderAdapter()

    # 1. Create Cube
    res_cube = adapter.create_primitive("CUBE", name="MyCube", location=[1.0, 2.0, 3.0], size=2.0)
    assert res_cube.success, f"Cube creation failed: {res_cube.error}"
    assert "MyCube" in bpy.data.objects
    cube_obj = bpy.data.objects["MyCube"]
    assert len(cube_obj.data.vertices) == 8
    assert len(cube_obj.data.polygons) == 6
    assert list(cube_obj.location) == [1.0, 2.0, 3.0]
    assert res_cube.data["created"] is True
    assert res_cube.data["exists"] is True
    assert res_cube.data["type"] == "MESH"
    assert res_cube.data["vertex_count"] == 8

    # 2. Create Sphere
    res_sphere = adapter.create_primitive("SPHERE", name="MySphere", location=[0.0, 0.0, 0.0], size=1.0)
    assert res_sphere.success, f"Sphere creation failed: {res_sphere.error}"
    assert "MySphere" in bpy.data.objects
    assert res_sphere.data["exists"] is True
    assert res_sphere.data["type"] == "MESH"
    sphere_obj = bpy.data.objects["MySphere"]
    assert len(sphere_obj.data.vertices) > 200
    assert len(sphere_obj.data.polygons) > 200

    # 3. Create Plane
    res_plane = adapter.create_primitive("PLANE", name="MyPlane", size=4.0)
    assert res_plane.success, f"Plane creation failed: {res_plane.error}"
    assert "MyPlane" in bpy.data.objects
    assert res_plane.data["exists"] is True
    assert res_plane.data["type"] == "MESH"
    plane_obj = bpy.data.objects["MyPlane"]
    assert len(plane_obj.data.vertices) == 4
    assert len(plane_obj.data.polygons) == 1

    # 4. Invalid primitive type
    res_invalid = adapter.create_primitive("DONUT")
    assert not res_invalid.success
    assert res_invalid.error.type == "INVALID_PRIMITIVE_TYPE"

    print("[PASS] Test 3: CUBE, SPHERE, PLANE creation and error handling verified.")


def test_transform_object():
    print("Test 4: Live object transformation (loc, rot, scale, relative)...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive("CUBE", name="TransformTarget", location=[0.0, 0.0, 0.0])
    target = bpy.data.objects["TransformTarget"]

    # Absolute transform
    res1 = adapter.transform_object(
        name="TransformTarget",
        location=[2.0, 3.0, 4.0],
        rotation=[0.0, 0.0, 1.5708],
        scale=[1.5, 1.5, 2.0],
    )
    assert res1.success, f"Absolute transform failed: {res1.error}"
    assert list(target.location) == [2.0, 3.0, 4.0]
    assert round(target.rotation_euler.z, 3) == 1.571
    assert list(target.scale) == [1.5, 1.5, 2.0]
    assert res1.data["changed"] == ["location", "rotation", "scale"]
    assert res1.data["exists"] is True
    assert res1.data["before"]["location"] == [0.0, 0.0, 0.0]
    assert res1.data["after"]["location"] == [2.0, 3.0, 4.0]
    assert res1.data["actual"]["location"] == [2.0, 3.0, 4.0]
    assert res1.data["actual"]["exists"] is True

    # Relative transform
    res2 = adapter.transform_object(
        name="TransformTarget",
        location=[1.0, -1.0, 0.0],
        scale=[2.0, 1.0, 1.0],
        relative=True,
    )
    assert res2.success
    assert list(target.location) == [3.0, 2.0, 4.0]
    assert list(target.scale) == [3.0, 1.5, 2.0]
    assert res2.data["relative"] is True
    assert res2.data["exists"] is True

    # Missing object
    res_missing = adapter.transform_object(name="NonExistentObject", location=[1, 1, 1])
    assert not res_missing.success
    assert res_missing.error.type == "OBJECT_NOT_FOUND"

    print("[PASS] Test 4: Live object transformations (absolute & relative) verified.")


def test_delete_object():
    print("Test 5: Live object deletion...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive("CUBE", name="DeleteTarget")
    assert "DeleteTarget" in bpy.data.objects

    res_del = adapter.delete_object(name="DeleteTarget")
    assert res_del.success, f"Delete failed: {res_del.error}"
    assert "DeleteTarget" not in bpy.data.objects
    assert res_del.data["deleted"] is True
    assert res_del.data["exists"] is False
    assert res_del.data["object_name"] == "DeleteTarget"

    # Deleting again raises OBJECT_NOT_FOUND
    res_del2 = adapter.delete_object(name="DeleteTarget")
    assert not res_del2.success
    assert res_del2.error.type == "OBJECT_NOT_FOUND"

    print("[PASS] Test 5: Live object deletion and missing-object guard verified.")


def test_acceptance_scenario_1_multi_step_undo():
    print("Test 6: Acceptance Scenario 1 — Create -> Move -> Delete -> Undo Sequence...")
    clean_scene()
    adapter = BlenderAdapter()

    # Step 1: Create Cube
    res1 = adapter.create_primitive("CUBE", name="AccCube", location=[0.0, 0.0, 0.0])
    assert res1.success
    assert "AccCube" in bpy.data.objects

    # Step 2: Transform Cube
    res2 = adapter.transform_object("AccCube", location=[0.0, 0.0, 2.0], scale=[2.0, 2.0, 2.0])
    assert res2.success
    assert list(bpy.data.objects["AccCube"].location) == [0.0, 0.0, 2.0]
    assert list(bpy.data.objects["AccCube"].scale) == [2.0, 2.0, 2.0]

    # Step 3: Delete Cube
    res3 = adapter.delete_object("AccCube")
    assert res3.success
    assert "AccCube" not in bpy.data.objects

    # --- REVERSE WITH UNDO ---

    # Undo Step 3 (revert Delete)
    assert perform_undo(), "Undo Step 3 failed"
    assert "AccCube" in bpy.data.objects, "AccCube was not restored after undoing delete"
    cube = bpy.data.objects["AccCube"]
    assert list(cube.location) == [0.0, 0.0, 2.0], f"Expected location [0,0,2], got {list(cube.location)}"
    assert list(cube.scale) == [2.0, 2.0, 2.0], f"Expected scale [2,2,2], got {list(cube.scale)}"

    # Undo Step 2 (revert Transform)
    assert perform_undo(), "Undo Step 2 failed"
    assert "AccCube" in bpy.data.objects
    cube = bpy.data.objects["AccCube"]
    assert list(cube.location) == [0.0, 0.0, 0.0], f"Expected location [0,0,0], got {list(cube.location)}"
    assert list(cube.scale) == [1.0, 1.0, 1.0], f"Expected scale [1,1,1], got {list(cube.scale)}"

    # Undo Step 1 (revert Create)
    assert perform_undo(), "Undo Step 1 failed"
    assert "AccCube" not in bpy.data.objects, "AccCube was not removed after undoing creation"
    assert len(bpy.data.objects) == 0, f"Scene not empty after full undo, remaining: {list(bpy.data.objects)}"

    print("[PASS] Test 6: Acceptance Scenario 1 multi-step undo sequence verified.")


def test_acceptance_scenario_2_create_transform_undo_redo():
    print("Test 7: Acceptance Scenario 2 — Create -> Transform -> Undo -> Redo...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create Cube at [1, 1, 1]
    res1 = adapter.create_primitive("CUBE", name="StepCube", location=[1.0, 1.0, 1.0])
    assert res1.success
    assert list(bpy.data.objects["StepCube"].location) == [1.0, 1.0, 1.0]

    # Transform to [5, 5, 5]
    res2 = adapter.transform_object("StepCube", location=[5.0, 5.0, 5.0])
    assert res2.success
    assert list(bpy.data.objects["StepCube"].location) == [5.0, 5.0, 5.0]

    # Undo transform -> should revert back to [1, 1, 1]
    assert perform_undo(), "Undo failed"
    assert list(bpy.data.objects["StepCube"].location) == [1.0, 1.0, 1.0], (
        f"Expected [1,1,1] after undo, got {list(bpy.data.objects['StepCube'].location)}"
    )

    # Redo transform -> should re-apply [5, 5, 5]
    assert perform_redo(), "Redo failed"
    assert list(bpy.data.objects["StepCube"].location) == [5.0, 5.0, 5.0], (
        f"Expected [5,5,5] after redo, got {list(bpy.data.objects['StepCube'].location)}"
    )

    print("[PASS] Test 7: Acceptance Scenario 2 create -> transform -> undo -> redo verified.")


def main():
    print("\n========================================================")
    print("   RUNNING M3.1 MUTATIONS & UNDO INTEGRATION TESTS     ")
    print("========================================================\n")

    test_tool_registration()
    test_thread_safety_guards()
    test_create_primitive()
    test_transform_object()
    test_delete_object()
    test_acceptance_scenario_1_multi_step_undo()
    test_acceptance_scenario_2_create_transform_undo_redo()

    print("\n========================================================")
    print("   ALL M3.1 MUTATION & UNDO TESTS PASSED (7/7)          ")
    print("========================================================\n")


if __name__ == "__main__":
    main()
