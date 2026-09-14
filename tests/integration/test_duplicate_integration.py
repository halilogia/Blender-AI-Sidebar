"""Integration tests for M9 Task 6: duplicate_object in headless Blender.

Verifies live object and datablock duplication, material preservation, custom transforms,
fail-closed name collision handling, source immutability, undo restoration, thread safety,
and M5 ChangeVerifier integration.
"""

import sys
import threading
import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from adapter.mutators.undo_manager import perform_undo, perform_redo, push_undo_step
from agent.verifier import ChangeVerifier, build_change_set_from_result
from core.change_set import VerificationStatus
from tools.registry import ToolRegistry
from tools.mutations.duplicate_object import DuplicateObjectTool


def clean_scene():
    """Reset scene to a completely empty state and initialize baseline undo point."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    push_undo_step("Initial Scene Baseline")


def test_tool_registration():
    print("Test 1: Tool registration and risk metadata...")
    registry = ToolRegistry()
    tool = DuplicateObjectTool()
    registry.register(tool)

    assert registry.exists("duplicate_object")
    assert tool.risk_level.value == "LOW"
    print("[PASS] Test 1: Tool registration verified.")


def test_thread_safety_guard():
    print("Test 2: Thread safety guard...")
    adapter = BlenderAdapter()
    caught_errors = []

    def background_worker():
        try:
            adapter.duplicate_object(source_name="NonExistent")
        except ThreadSafetyViolationError:
            caught_errors.append("duplicate_object")

    thread = threading.Thread(target=background_worker)
    thread.start()
    thread.join()

    assert caught_errors == ["duplicate_object"], (
        f"Expected ThreadSafetyViolationError on background thread, got {caught_errors}"
    )
    print("[PASS] Test 2: ThreadSafetyViolationError strictly enforced on background thread.")


def test_duplicate_mesh_default_name():
    print("Test 3: Duplicate mesh with deterministic default naming...")
    clean_scene()
    adapter = BlenderAdapter()

    # 1. Create a base cube
    create_res = adapter.create_primitive(primitive_type="CUBE", name="SourceCube", location=[0.0, 0.0, 0.0])
    assert create_res.success, f"Failed to create primitive: {create_res.message}"

    # 2. Duplicate without specifying new_name
    dup_res = adapter.duplicate_object(source_name="SourceCube")
    assert dup_res.success, f"Duplicate failed: {dup_res.message}"
    data = dup_res.data

    assert data["duplicated"] is True
    assert data["source_name"] == "SourceCube"
    assert data["new_name"] == "SourceCube_copy_1"
    assert data["source_exists"] is True
    assert data["new_exists"] is True
    assert data["distinct_identity"] is True
    assert data["distinct_data"] is True

    # Verify Blender objects
    src_obj = bpy.data.objects.get("SourceCube")
    dup_obj = bpy.data.objects.get("SourceCube_copy_1")
    assert src_obj is not None
    assert dup_obj is not None
    assert src_obj != dup_obj
    assert src_obj.data != dup_obj.data

    # Duplicate again without name -> should generate SourceCube_copy_2
    dup_res2 = adapter.duplicate_object(source_name="SourceCube")
    assert dup_res2.success
    assert dup_res2.data["new_name"] == "SourceCube_copy_2"
    assert bpy.data.objects.get("SourceCube_copy_2") is not None

    print("[PASS] Test 3: Duplicate mesh with deterministic default naming verified.")


def test_duplicate_transforms_and_custom_name():
    print("Test 4: Duplicate with custom name and explicit transforms...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive(primitive_type="CUBE", name="Chair", location=[0.0, 0.0, 0.0])

    dup_res = adapter.duplicate_object(
        source_name="Chair",
        new_name="Chair_Clone",
        location=[3.0, 4.0, 5.0],
        rotation=[0.0, 1.5708, 0.0],
        scale=[2.0, 2.0, 2.0],
    )
    assert dup_res.success, f"Duplicate failed: {dup_res.message}"
    data = dup_res.data

    assert data["new_name"] == "Chair_Clone"
    assert data["location"] == [3.0, 4.0, 5.0]
    assert data["scale"] == [2.0, 2.0, 2.0]

    clone_obj = bpy.data.objects.get("Chair_Clone")
    assert clone_obj is not None
    assert round(clone_obj.location.x, 2) == 3.0
    assert round(clone_obj.location.y, 2) == 4.0
    assert round(clone_obj.location.z, 2) == 5.0
    assert round(clone_obj.scale.x, 2) == 2.0

    print("[PASS] Test 4: Custom name and transforms verified.")


def test_material_preservation():
    print("Test 5: Material preservation on duplicated object...")
    clean_scene()
    adapter = BlenderAdapter()

    # Create primitive and material
    adapter.create_primitive(primitive_type="CUBE", name="MatCube")
    adapter.set_material(object_name="MatCube", material_name="OakWood", base_color=[0.8, 0.5, 0.2, 1.0])

    src_obj = bpy.data.objects.get("MatCube")
    assert len(src_obj.material_slots) == 1
    assert src_obj.material_slots[0].material.name == "OakWood"

    # Duplicate object
    dup_res = adapter.duplicate_object(source_name="MatCube", new_name="MatCube_Dupe")
    assert dup_res.success, f"Duplicate failed: {dup_res.message}"

    dupe_obj = bpy.data.objects.get("MatCube_Dupe")
    assert dupe_obj is not None
    assert len(dupe_obj.material_slots) == 1
    assert dupe_obj.material_slots[0].material.name == "OakWood"
    assert dup_res.data["materials"] == ["OakWood"]

    # Verify datablocks are separate while referencing same material
    assert dupe_obj.data != src_obj.data
    assert dupe_obj.material_slots[0].material == src_obj.material_slots[0].material

    print("[PASS] Test 5: Material preservation verified.")


def test_source_unchanged():
    print("Test 6: Source object immutability during duplication...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive(
        primitive_type="CUBE",
        name="Untouched",
        location=[1.0, 2.0, 3.0],
        rotation=[0.1, 0.2, 0.3],
        scale=[0.5, 0.5, 0.5],
    )
    src_obj = bpy.data.objects.get("Untouched")
    orig_loc = list(src_obj.location)
    orig_rot = list(src_obj.rotation_euler)
    orig_scale = list(src_obj.scale)
    orig_vcount = len(src_obj.data.vertices)

    # Perform multiple duplications with different transforms
    for i in range(3):
        res = adapter.duplicate_object(
            source_name="Untouched",
            location=[float(i * 10), 0.0, 0.0],
            scale=[float(i + 1), float(i + 1), float(i + 1)],
        )
        assert res.success

    # Check source object
    assert list(src_obj.location) == orig_loc
    assert list(src_obj.rotation_euler) == orig_rot
    assert list(src_obj.scale) == orig_scale
    assert len(src_obj.data.vertices) == orig_vcount
    assert src_obj.name == "Untouched"

    print("[PASS] Test 6: Source immutability verified.")


def test_name_collision_fail_closed():
    print("Test 7: Name collision fail-closed behavior...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive(primitive_type="CUBE", name="Original")
    adapter.create_primitive(primitive_type="CUBE", name="ExistingClone")

    # Attempt to duplicate with new_name="ExistingClone"
    fail_res = adapter.duplicate_object(source_name="Original", new_name="ExistingClone")
    assert not fail_res.success
    assert fail_res.error is not None
    assert fail_res.error.type == "INVALID_ARGUMENT"
    assert "already exists" in fail_res.error.message

    # Attempt to duplicate with new_name="Original" (same as source)
    fail_res2 = adapter.duplicate_object(source_name="Original", new_name="Original")
    assert not fail_res2.success
    assert fail_res2.error is not None
    assert fail_res2.error.type == "INVALID_ARGUMENT"
    assert "already exists" in fail_res2.error.message

    print("[PASS] Test 7: Name collision fail-closed verified.")


def test_invalid_source_fail_closed():
    print("Test 8: Invalid source object fail-closed...")
    clean_scene()
    adapter = BlenderAdapter()

    fail_res = adapter.duplicate_object(source_name="NonExistentGhost")
    assert not fail_res.success
    assert fail_res.error is not None
    assert fail_res.error.type == "OBJECT_NOT_FOUND"
    assert "not found" in fail_res.error.message.lower()

    # Empty string
    fail_res2 = adapter.duplicate_object(source_name="")
    assert not fail_res2.success
    assert fail_res2.error is not None
    assert fail_res2.error.type == "INVALID_ARGUMENT"

    print("[PASS] Test 8: Invalid source fail-closed verified.")


def test_undo_redo():
    print("Test 9: Atomic undo/redo restoration...")
    clean_scene()
    adapter = BlenderAdapter()

    adapter.create_primitive(primitive_type="CUBE", name="UndoBase")
    dup_res = adapter.duplicate_object(source_name="UndoBase", new_name="UndoCopy", location=[5.0, 0.0, 0.0])
    assert dup_res.success
    assert bpy.data.objects.get("UndoCopy") is not None

    # Undo duplication
    perform_undo()
    assert bpy.data.objects.get("UndoCopy") is None, "UndoCopy should not exist after undo"
    assert bpy.data.objects.get("UndoBase") is not None, "UndoBase must still exist"

    # Redo duplication
    perform_redo()
    assert bpy.data.objects.get("UndoCopy") is not None, "UndoCopy should be restored after redo"

    print("[PASS] Test 9: Undo/redo verified.")


def test_verification_integration():
    print("Test 10: M5 ChangeVerifier end-to-end integration...")
    clean_scene()
    adapter = BlenderAdapter()
    verifier = ChangeVerifier()

    adapter.create_primitive(primitive_type="CUBE", name="VSource", location=[0.0, 0.0, 0.0])

    args = {
        "source_name": "VSource",
        "new_name": "VDuplicate",
        "location": [2.0, 3.0, 4.0],
        "rotation": [0.0, 0.0, 1.5708],
        "scale": [1.0, 1.0, 1.0],
    }

    dup_res = adapter.duplicate_object(**args)
    assert dup_res.success, f"Duplicate failed: {dup_res.message}"

    cs = build_change_set_from_result("duplicate_object", args, dup_res.data)
    assert cs is not None
    assert cs.operation == "duplicate_object"
    assert cs.target_name == "VDuplicate"

    v_res = verifier.verify(cs)
    assert v_res.status == VerificationStatus.PASS, f"Verification failed with mismatches: {v_res.mismatches}"
    assert len(v_res.mismatches) == 0

    print("[PASS] Test 10: M5 ChangeVerifier end-to-end integration verified.")


def run_all():
    print("\n========================================================")
    print("   RUNNING M9 TASK 6 DUPLICATE OBJECT INTEGRATION TESTS  ")
    print("========================================================\n")

    test_tool_registration()
    test_thread_safety_guard()
    test_duplicate_mesh_default_name()
    test_duplicate_transforms_and_custom_name()
    test_material_preservation()
    test_source_unchanged()
    test_name_collision_fail_closed()
    test_invalid_source_fail_closed()
    test_undo_redo()
    test_verification_integration()

    print("\n========================================================")
    print("   ALL M9 TASK 6 DUPLICATE OBJECT TESTS PASSED (10/10)   ")
    print("========================================================\n")


if __name__ == "__main__":
    try:
        run_all()
    except Exception as e:
        print(f"\n[FAIL] Test suite failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
