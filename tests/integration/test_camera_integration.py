"""Integration tests for M9 Task 3: Camera Semantic Tool in headless Blender.

Verifies live camera creation, modification, parameter validation, active camera assignment,
thread safety guards, undo state restoration, and M5 ChangeVerifier integration.
"""

import sys
import threading
import bpy
from mathutils import Euler, Vector

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from adapter.mutators.undo_manager import perform_undo, perform_redo, push_undo_step
from agent.verifier import ChangeVerifier, build_change_set_from_result
from tools.registry import ToolRegistry
from tools.mutations.create_camera import CreateCameraTool


def clean_scene():
    """Reset scene to a completely empty state and initialize baseline undo point."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    push_undo_step("Initial Scene Baseline")


def test_tool_registration():
    print("Test 1: Tool registration and metadata...")
    registry = ToolRegistry()
    cam_tool = CreateCameraTool()
    registry.register(cam_tool)

    assert registry.exists("create_camera"), "create_camera must be registered"
    retrieved = registry.get("create_camera")
    assert retrieved.name == "create_camera"
    assert retrieved.risk_level.value == "LOW"
    print("[PASS] Test 1: Tool registration and metadata verified.")


def test_thread_safety_guards():
    print("Test 2: Thread safety guards on create_camera...")
    adapter = BlenderAdapter()
    caught_errors = []

    def background_worker():
        try:
            adapter.create_camera()
        except ThreadSafetyViolationError:
            caught_errors.append("create_camera")

    thread = threading.Thread(target=background_worker)
    thread.start()
    thread.join()

    assert caught_errors == ["create_camera"], (
        f"Expected ThreadSafetyViolationError, got {caught_errors}"
    )
    print("[PASS] Test 2: ThreadSafetyViolationError strictly enforced on background thread.")


def test_create_camera_defaults():
    print("Test 3: Camera creation with defaults...")
    clean_scene()
    adapter = BlenderAdapter()

    res = adapter.create_camera()
    assert res.success, f"Camera creation failed: {res.error}"
    assert "Camera" in bpy.data.objects, "Camera object not found in bpy.data.objects"
    cam_obj = bpy.data.objects["Camera"]
    assert cam_obj.type == "CAMERA", f"Expected type CAMERA, got {cam_obj.type}"
    assert abs(cam_obj.data.lens - 50.0) < 1e-3, f"Expected lens 50.0, got {cam_obj.data.lens}"
    assert bpy.context.scene.camera == cam_obj, "Created camera must be set as active scene camera by default"
    assert list(cam_obj.location) == [0.0, 0.0, 0.0], f"Expected origin location, got {list(cam_obj.location)}"

    # Snapshot contract
    data = res.data
    assert data["created"] is True
    assert data["exists"] is True
    assert data["object_name"] == "Camera"
    assert data["type"] == "CAMERA"
    assert data["lens"] == 50.0
    assert data["is_active_camera"] is True

    print("[PASS] Test 3: Camera creation with defaults verified.")


def test_create_camera_custom():
    print("Test 4: Camera creation with custom properties...")
    clean_scene()
    adapter = BlenderAdapter()

    # First create default camera
    adapter.create_camera(name="DefaultCam")

    # Now create custom camera without making active
    res = adapter.create_camera(
        name="TelephotoCam",
        location=[5.0, -10.0, 3.5],
        rotation=[1.2, 0.0, 0.8],
        lens=135.0,
        make_active=False,
    )
    assert res.success, f"Custom camera creation failed: {res.error}"
    assert "TelephotoCam" in bpy.data.objects
    cam_obj = bpy.data.objects["TelephotoCam"]
    assert cam_obj.type == "CAMERA"
    assert abs(cam_obj.data.lens - 135.0) < 1e-3
    assert abs(cam_obj.location.x - 5.0) < 1e-3
    assert abs(cam_obj.location.y - (-10.0)) < 1e-3
    assert abs(cam_obj.location.z - 3.5) < 1e-3
    assert abs(cam_obj.rotation_euler.x - 1.2) < 1e-3
    assert abs(cam_obj.rotation_euler.z - 0.8) < 1e-3
    assert bpy.context.scene.camera.name == "DefaultCam", "make_active=False must not change scene camera"

    print("[PASS] Test 4: Camera creation with custom properties verified.")


def test_modify_existing_camera():
    print("Test 5: Modifying existing camera in-place...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_camera(name="StudioCam", lens=50.0, make_active=False)
    initial_count = len([o for o in bpy.data.objects if o.type == "CAMERA"])
    assert initial_count == 1

    # Modify existing StudioCam
    res = adapter.create_camera(
        name="StudioCam",
        location=[0.0, -8.0, 2.0],
        lens=85.0,
        make_active=True,
    )
    assert res.success, f"Modify existing camera failed: {res.error}"
    assert res.data["created"] is False, "Modified camera should report created=False"
    after_count = len([o for o in bpy.data.objects if o.type == "CAMERA"])
    assert after_count == 1, "Modifying existing camera must not duplicate camera objects"

    cam_obj = bpy.data.objects["StudioCam"]
    assert abs(cam_obj.data.lens - 85.0) < 1e-3
    assert abs(cam_obj.location.y - (-8.0)) < 1e-3
    assert bpy.context.scene.camera == cam_obj, "StudioCam should now be active scene camera"

    print("[PASS] Test 5: Modifying existing camera in-place verified.")


def test_fail_closed_on_non_camera_collision():
    print("Test 6: Fail-closed on non-camera object name collision...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create primitive mesh Cube
    res_cube = adapter.create_primitive("CUBE", name="CollidingName")
    assert res_cube.success

    # Attempt to create camera with same name
    res_cam = adapter.create_camera(name="CollidingName")
    assert not res_cam.success, "Should fail when object exists and is not a CAMERA"
    assert "already exists but is of type 'MESH'" in res_cam.error.message

    # Ensure Cube was not altered
    obj = bpy.data.objects["CollidingName"]
    assert obj.type == "MESH"

    print("[PASS] Test 6: Fail-closed on non-camera collision verified.")


def test_fail_closed_on_invalid_parameters():
    print("Test 7: Fail-closed on invalid parameters...")
    clean_scene()
    adapter = BlenderAdapter()

    # Invalid lens <= 0
    res1 = adapter.create_camera(lens=0.0)
    assert not res1.success
    assert "strictly positive" in res1.error.message

    res2 = adapter.create_camera(lens=-24.0)
    assert not res2.success
    assert "strictly positive" in res2.error.message

    # Invalid location format
    res3 = adapter.create_camera(location=[1.0, 2.0])  # only 2 elements
    assert not res3.success
    assert "sequence of 3 numbers" in res3.error.message

    # Invalid rotation format
    res4 = adapter.create_camera(rotation=[0.0, 1.0])
    assert not res4.success
    assert "sequence of 3 numbers" in res4.error.message

    print("[PASS] Test 7: Fail-closed on invalid parameters verified.")


def test_camera_undo_restoration():
    print("Test 8: Camera undo restoration...")
    clean_scene()
    adapter = BlenderAdapter()

    res = adapter.create_camera(name="TransientCam")
    assert res.success
    assert "TransientCam" in bpy.data.objects

    # Undo
    perform_undo()
    assert "TransientCam" not in bpy.data.objects, "Undo must remove created camera"

    # Redo
    perform_redo()
    assert "TransientCam" in bpy.data.objects, "Redo must restore created camera"

    print("[PASS] Test 8: Camera undo restoration verified.")


def test_camera_verification_integration():
    print("Test 9: M5 ChangeVerifier integration...")
    clean_scene()
    adapter = BlenderAdapter()
    verifier = ChangeVerifier()

    args = {
        "name": "VerifiedCam",
        "location": [2.0, -6.0, 1.5],
        "rotation": [0.5, 0.0, 0.0],
        "lens": 35.0,
        "make_active": True,
    }
    res = adapter.create_camera(**args)
    assert res.success

    cs = build_change_set_from_result("create_camera", args, res.data)
    assert cs is not None
    assert cs.operation == "create_camera"
    assert cs.target_name == "VerifiedCam"

    verification = verifier.verify(cs)
    assert verification.status.value == "PASS", f"Verification failed: {verification.mismatches}"
    assert verification.passed is True
    print("[PASS] Test 9: M5 ChangeVerifier integration verified.")


def main():
    print("\n========================================================")
    print("   RUNNING CAMERA SEMANTIC TOOL INTEGRATION TESTS       ")
    print("========================================================\n")

    test_tool_registration()
    test_thread_safety_guards()
    test_create_camera_defaults()
    test_create_camera_custom()
    test_modify_existing_camera()
    test_fail_closed_on_non_camera_collision()
    test_fail_closed_on_invalid_parameters()
    test_camera_undo_restoration()
    test_camera_verification_integration()

    print("\n========================================================")
    print("   ALL CAMERA INTEGRATION TESTS PASSED (9/9)            ")
    print("========================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
