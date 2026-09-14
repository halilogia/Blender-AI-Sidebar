"""Integration tests for M9 Task 4: Light Semantic Tool in headless Blender.

Verifies live light creation, type handling (POINT, SUN, SPOT, AREA), modification,
parameter validation, thread safety guards, undo state restoration, and M5 ChangeVerifier integration.
"""

import sys
import threading
import bpy
from mathutils import Euler, Vector

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from adapter.mutators.undo_manager import perform_undo, perform_redo, push_undo_step
from agent.verifier import ChangeVerifier, build_change_set_from_result
from tools.registry import ToolRegistry
from tools.mutations.create_light import CreateLightTool


def clean_scene():
    """Reset scene to a completely empty state and initialize baseline undo point."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    push_undo_step("Initial Scene Baseline")


def test_tool_registration():
    print("Test 1: Tool registration and metadata...")
    registry = ToolRegistry()
    light_tool = CreateLightTool()
    registry.register(light_tool)

    assert registry.exists("create_light"), "create_light must be registered"
    retrieved = registry.get("create_light")
    assert retrieved.name == "create_light"
    assert retrieved.risk_level.value == "LOW"
    print("[PASS] Test 1: Tool registration and metadata verified.")


def test_thread_safety_guards():
    print("Test 2: Thread safety guards on create_light...")
    adapter = BlenderAdapter()
    caught_errors = []

    def background_worker():
        try:
            adapter.create_light()
        except ThreadSafetyViolationError:
            caught_errors.append("create_light")

    thread = threading.Thread(target=background_worker)
    thread.start()
    thread.join()

    assert caught_errors == ["create_light"], (
        f"Expected ThreadSafetyViolationError, got {caught_errors}"
    )
    print("[PASS] Test 2: ThreadSafetyViolationError strictly enforced on background thread.")


def test_create_point_light_defaults():
    print("Test 3: Point light creation with defaults...")
    clean_scene()
    adapter = BlenderAdapter()

    res = adapter.create_light()
    assert res.success, f"Light creation failed: {res.error}"
    assert "Light" in bpy.data.objects, "Light object not found in bpy.data.objects"
    light_obj = bpy.data.objects["Light"]
    assert light_obj.type == "LIGHT", f"Expected object type LIGHT, got {light_obj.type}"
    assert light_obj.data.type == "POINT", f"Expected light data type POINT, got {light_obj.data.type}"
    assert abs(light_obj.data.energy - 10.0) < 1e-3, f"Expected energy 10.0, got {light_obj.data.energy}"
    assert list(light_obj.location) == [0.0, 0.0, 0.0]
    assert list(light_obj.data.color) == [1.0, 1.0, 1.0]

    # Snapshot contract
    data = res.data
    assert data["created"] is True
    assert data["exists"] is True
    assert data["object_name"] == "Light"
    assert data["type"] == "LIGHT"
    assert data["light_type"] == "POINT"
    assert data["energy"] == 10.0
    assert data["color"] == [1.0, 1.0, 1.0]

    print("[PASS] Test 3: Point light creation with defaults verified.")


def test_create_other_light_types():
    print("Test 4: Sun, Spot, and Area light types...")
    clean_scene()
    adapter = BlenderAdapter()

    # 1. Sun
    res_sun = adapter.create_light(name="SunLight", light_type="SUN", energy=5.0)
    assert res_sun.success
    sun_obj = bpy.data.objects["SunLight"]
    assert sun_obj.data.type == "SUN"
    assert abs(sun_obj.data.energy - 5.0) < 1e-3

    # 2. Spot
    res_spot = adapter.create_light(name="SpotLight", light_type="SPOT", energy=100.0)
    assert res_spot.success
    spot_obj = bpy.data.objects["SpotLight"]
    assert spot_obj.data.type == "SPOT"
    assert abs(spot_obj.data.energy - 100.0) < 1e-3

    # 3. Area
    res_area = adapter.create_light(name="AreaLight", light_type="AREA", energy=250.0)
    assert res_area.success
    area_obj = bpy.data.objects["AreaLight"]
    assert area_obj.data.type == "AREA"
    assert abs(area_obj.data.energy - 250.0) < 1e-3

    print("[PASS] Test 4: Sun, Spot, and Area light types verified.")


def test_transform_and_color():
    print("Test 5: Light transform (location, rotation) and color...")
    clean_scene()
    adapter = BlenderAdapter()

    res = adapter.create_light(
        name="KeyLight",
        light_type="SPOT",
        location=[3.0, -4.0, 6.0],
        rotation=[0.785, 0.0, 0.523],
        energy=500.0,
        color=[1.0, 0.8, 0.6],
    )
    assert res.success
    obj = bpy.data.objects["KeyLight"]
    assert abs(obj.location.x - 3.0) < 1e-3
    assert abs(obj.location.y - (-4.0)) < 1e-3
    assert abs(obj.location.z - 6.0) < 1e-3
    assert abs(obj.rotation_euler.x - 0.785) < 1e-3
    assert abs(obj.rotation_euler.z - 0.523) < 1e-3
    assert abs(obj.data.color[0] - 1.0) < 1e-3
    assert abs(obj.data.color[1] - 0.8) < 1e-3
    assert abs(obj.data.color[2] - 0.6) < 1e-3

    print("[PASS] Test 5: Light transform and color verified.")


def test_modify_existing_light():
    print("Test 6: Modifying existing light in-place...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_light(name="MutableLight", light_type="POINT", energy=50.0)
    initial_count = len([o for o in bpy.data.objects if o.type == "LIGHT"])
    assert initial_count == 1

    # Modify existing MutableLight: switch to SUN, change energy & location
    res = adapter.create_light(
        name="MutableLight",
        light_type="SUN",
        location=[0.0, 0.0, 10.0],
        energy=2.0,
    )
    assert res.success
    assert res.data["created"] is False, "Modified light must report created=False"
    after_count = len([o for o in bpy.data.objects if o.type == "LIGHT"])
    assert after_count == 1, "Modifying existing light must not duplicate objects"

    obj = bpy.data.objects["MutableLight"]
    assert obj.data.type == "SUN"
    assert abs(obj.data.energy - 2.0) < 1e-3
    assert abs(obj.location.z - 10.0) < 1e-3

    print("[PASS] Test 6: Modifying existing light in-place verified.")


def test_fail_closed_on_non_light_collision():
    print("Test 7: Fail-closed on non-light object name collision...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create primitive mesh Cube
    res_cube = adapter.create_primitive("CUBE", name="CollidingLightName")
    assert res_cube.success

    # Attempt to create light with same name
    res_light = adapter.create_light(name="CollidingLightName")
    assert not res_light.success, "Should fail when object exists and is not a LIGHT"
    assert "already exists but is of type 'MESH'" in res_light.error.message

    # Ensure Cube was not altered
    obj = bpy.data.objects["CollidingLightName"]
    assert obj.type == "MESH"

    print("[PASS] Test 7: Fail-closed on non-light collision verified.")


def test_fail_closed_on_invalid_parameters():
    print("Test 8: Fail-closed on invalid parameters...")
    clean_scene()
    adapter = BlenderAdapter()

    # Invalid light_type
    res1 = adapter.create_light(light_type="LASER")
    assert not res1.success
    assert "Invalid light_type" in res1.error.message

    # Invalid energy < 0
    res2 = adapter.create_light(energy=-10.0)
    assert not res2.success
    assert "non-negative" in res2.error.message

    # Invalid color format
    res3 = adapter.create_light(color=[1.0, 0.0])  # only 2 elements
    assert not res3.success
    assert "sequence of 3 numbers" in res3.error.message

    # Invalid location format
    res4 = adapter.create_light(location=[0.0, 1.0])
    assert not res4.success
    assert "sequence of 3 numbers" in res4.error.message

    print("[PASS] Test 8: Fail-closed on invalid parameters verified.")


def test_light_undo_restoration():
    print("Test 9: Light undo restoration...")
    clean_scene()
    adapter = BlenderAdapter()

    res = adapter.create_light(name="UndoLight")
    assert res.success
    assert "UndoLight" in bpy.data.objects

    # Undo
    perform_undo()
    assert "UndoLight" not in bpy.data.objects, "Undo must remove created light"

    # Redo
    perform_redo()
    assert "UndoLight" in bpy.data.objects, "Redo must restore created light"

    print("[PASS] Test 9: Light undo restoration verified.")


def test_light_verification_integration():
    print("Test 10: M5 ChangeVerifier integration...")
    clean_scene()
    adapter = BlenderAdapter()
    verifier = ChangeVerifier()

    args = {
        "name": "VerifiedLight",
        "light_type": "SUN",
        "location": [1.0, 2.0, 5.0],
        "rotation": [0.3, 0.0, 0.0],
        "energy": 150.0,
        "color": [1.0, 0.9, 0.8],
    }
    res = adapter.create_light(**args)
    assert res.success

    cs = build_change_set_from_result("create_light", args, res.data)
    assert cs is not None
    assert cs.operation == "create_light"
    assert cs.target_name == "VerifiedLight"

    verification = verifier.verify(cs)
    assert verification.status.value == "PASS", f"Verification failed: {verification.mismatches}"
    assert verification.passed is True
    print("[PASS] Test 10: M5 ChangeVerifier integration verified.")


def main():
    print("\n========================================================")
    print("   RUNNING LIGHT SEMANTIC TOOL INTEGRATION TESTS        ")
    print("========================================================\n")

    test_tool_registration()
    test_thread_safety_guards()
    test_create_point_light_defaults()
    test_create_other_light_types()
    test_transform_and_color()
    test_modify_existing_light()
    test_fail_closed_on_non_light_collision()
    test_fail_closed_on_invalid_parameters()
    test_light_undo_restoration()
    test_light_verification_integration()

    print("\n========================================================")
    print("   ALL LIGHT INTEGRATION TESTS PASSED (10/10)           ")
    print("========================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
