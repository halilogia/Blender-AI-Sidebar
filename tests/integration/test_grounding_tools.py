"""Headless integration test for Phase 3 and Phase 4 Grounding Tools.

Runs inside Blender 5.2.1 LTS with real bpy datablocks covering:
1. inspect_scene
2. inspect_selection
3. inspect_object
4. inspect_material
5. inspect_mesh
"""

import sys
import os
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from tools.read_only.inspect_scene import InspectSceneTool
from tools.read_only.inspect_selection import InspectSelectionTool
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_material import InspectMaterialTool
from tools.read_only.inspect_mesh import InspectMeshTool


def test_main_thread_guard():
    """Verify that accessing BlenderAdapter from a background thread raises ThreadSafetyViolationError."""
    adapter = BlenderAdapter()
    violation_caught = False

    def background_task():
        nonlocal violation_caught
        try:
            adapter.inspect_scene()
        except ThreadSafetyViolationError:
            violation_caught = True

    thread = threading.Thread(target=background_task)
    thread.start()
    thread.join()

    assert violation_caught, "Main-thread guard failed to catch background thread access!"
    print("[PASS] Main-thread safety guard verified.")


def test_inspect_scene(adapter):
    """Verify inspect_scene returns complete, deterministic schema on default scene."""
    tool = InspectSceneTool()
    result = tool.execute(adapter)

    assert result.success, f"inspect_scene failed: {result.error}"
    data = result.data

    assert data["scene_name"] == "Scene"
    assert data["unit_system"] in ("METRIC", "IMPERIAL", "NONE")
    assert "counts" in data
    assert data["counts"]["mesh"] >= 1
    assert data["counts"]["total"] >= 3

    # Check object summaries
    obj_names = [obj["name"] for obj in data["objects"]]
    assert "Cube" in obj_names
    assert "Camera" in obj_names
    assert "Light" in obj_names

    # Check determinism: objects must be sorted by name
    assert obj_names == sorted(obj_names), "Objects list is not sorted deterministically!"
    print("[PASS] inspect_scene returns complete, deterministic summary of default scene.")


def test_inspect_selection(adapter):
    """Verify inspect_selection when Cube is selected vs when nothing is selected."""
    tool = InspectSelectionTool()

    # Case A: Cube is active and selected
    cube = bpy.data.objects.get("Cube")
    if cube:
        bpy.context.view_layer.objects.active = cube
        cube.select_set(True)

    result_a = tool.execute(adapter)
    assert result_a.success, f"inspect_selection failed: {result_a.error}"
    assert result_a.data["active_object"] == "Cube"
    assert "Cube" in result_a.data["selected_objects"]
    assert result_a.data["selection_count"] >= 1
    print("[PASS] inspect_selection with active selection verified.")

    # Case B: Deselect everything and clear active object
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = None

    result_b = tool.execute(adapter)
    assert result_b.success, f"inspect_selection failed when unselected: {result_b.error}"
    assert result_b.data["active_object"] is None
    assert result_b.data["selected_objects"] == []
    assert result_b.data["selection_count"] == 0
    print("[PASS] inspect_selection with empty selection verified (no crash, active_object is null).")


def test_inspect_object_existing(adapter):
    """Verify inspect_object on Cube returns transforms, dimensions, modifiers, and materials."""
    tool = InspectObjectTool()
    result = tool.execute(adapter, name="Cube")

    assert result.success, f"inspect_object failed on Cube: {result.error}"
    data = result.data

    assert data["name"] == "Cube"
    assert data["type"] == "MESH"
    assert data["is_linked"] is False
    assert data["parent"] is None
    assert "Collection" in data["collections"]

    # Transforms
    assert len(data["transform"]["location"]) == 3
    assert len(data["transform"]["rotation_euler_deg"]) == 3
    assert len(data["transform"]["scale"]) == 3
    assert len(data["dimensions"]) == 3

    # Evaluated is null in M1
    assert data["evaluated"] is None

    # Material
    assert isinstance(data["materials"], list)
    assert "Material" in data["materials"]
    print("[PASS] inspect_object on existing object (Cube) verified.")


def test_inspect_object_without_material(adapter):
    """Verify inspect_object on an object without material slots returns empty materials list."""
    tool = InspectObjectTool()
    light = bpy.data.objects.get("Light")
    assert light is not None

    result = tool.execute(adapter, name="Light")
    assert result.success, f"inspect_object failed on Light: {result.error}"
    assert result.data["type"] == "LIGHT"
    assert result.data["materials"] == []
    print("[PASS] inspect_object on object without material returns clean empty list.")


def test_inspect_object_not_found(adapter):
    """Verify inspect_object on non-existent object returns structured OBJECT_NOT_FOUND error."""
    tool = InspectObjectTool()
    result = tool.execute(adapter, name="GhostObject_99")

    assert not result.success
    assert result.data is None
    assert result.error is not None
    assert result.error.type == "OBJECT_NOT_FOUND"
    assert result.error.details.get("queried_name") == "GhostObject_99"
    print("[PASS] inspect_object on non-existent object produces structured OBJECT_NOT_FOUND.")


def test_inspect_material_suite(adapter):
    """Verify all test matrix scenarios for inspect_material."""
    tool = InspectMaterialTool()

    # 1. Default Cube + default Material via object_name + slot_index
    res_slot = tool.execute(adapter, object_name="Cube", slot_index=0)
    assert res_slot.success, f"Failed inspect_material via slot: {res_slot.error}"
    data = res_slot.data
    assert data["material_name"] == "Material"
    assert data["use_nodes"] is True
    assert "Cube" in data["assigned_objects"]
    assert data["slot_binding"] == {"object_name": "Cube", "slot_index": 0}

    # Verify Principled BSDF fields
    bsdf = data["principled_bsdf"]
    assert bsdf is not None
    assert isinstance(bsdf["base_color"], list) and len(bsdf["base_color"]) == 4
    assert bsdf["metallic"] == 0.0
    assert bsdf["roughness"] == 0.5
    assert bsdf["alpha"] == 1.0

    # Verify node summary
    summary = data["node_summary"]
    assert summary is not None
    assert summary["node_count"] >= 2
    assert "BSDF_PRINCIPLED" in summary["node_types"]
    print("[PASS] inspect_material (object_name + slot_index) & Principled BSDF verified.")

    # 2. Direct material_name inspection
    res_direct = tool.execute(adapter, material_name="Material")
    assert res_direct.success, f"Failed direct inspect_material: {res_direct.error}"
    assert res_direct.data["material_name"] == "Material"
    assert res_direct.data["slot_binding"] is None
    print("[PASS] inspect_material (direct material_name) verified.")

    # 3. Multiple material slots
    cube = bpy.data.objects.get("Cube")
    gold_mat = bpy.data.materials.new(name="GoldMaterial")
    gold_mat.use_nodes = True
    cube.data.materials.append(gold_mat)

    res_slot_1 = tool.execute(adapter, object_name="Cube", slot_index=1)
    assert res_slot_1.success, f"Failed slot 1 inspection: {res_slot_1.error}"
    assert res_slot_1.data["material_name"] == "GoldMaterial"
    assert res_slot_1.data["slot_binding"]["slot_index"] == 1
    print("[PASS] inspect_material on multiple material slots verified.")

    # 4. Invalid slot index out of range
    res_invalid_slot = tool.execute(adapter, object_name="Cube", slot_index=99)
    assert not res_invalid_slot.success
    assert res_invalid_slot.error.type == "SLOT_INDEX_OUT_OF_RANGE"
    print("[PASS] inspect_material with out-of-range slot produces SLOT_INDEX_OUT_OF_RANGE.")

    # 5. Non-existent material name
    res_ghost_mat = tool.execute(adapter, material_name="GhostMaterial_XYZ")
    assert not res_ghost_mat.success
    assert res_ghost_mat.error.type == "MATERIAL_NOT_FOUND"
    print("[PASS] inspect_material with non-existent material produces MATERIAL_NOT_FOUND.")

    # 6. Non-existent object name
    res_ghost_obj = tool.execute(adapter, object_name="GhostObject_ABC", slot_index=0)
    assert not res_ghost_obj.success
    assert res_ghost_obj.error.type == "OBJECT_NOT_FOUND"
    print("[PASS] inspect_material with non-existent object produces OBJECT_NOT_FOUND.")

    # 7. Object without material slots (Light has 0 slots)
    res_light = tool.execute(adapter, object_name="Light", slot_index=0)
    assert not res_light.success
    assert res_light.error.type == "SLOT_INDEX_OUT_OF_RANGE"
    print("[PASS] inspect_material on object without material slots handled safely.")

    # 8. Material without Principled BSDF (custom/cleared node tree)
    custom_mat = bpy.data.materials.new(name="CustomShaderMaterial")
    custom_mat.node_tree.nodes.clear()
    res_custom = tool.execute(adapter, material_name="CustomShaderMaterial")
    assert res_custom.success
    assert res_custom.data["principled_bsdf"] is None
    assert res_custom.data["node_summary"]["node_count"] == 0
    assert res_custom.data["node_summary"]["node_types"] == []
    print("[PASS] inspect_material without Principled BSDF handled cleanly.")


def test_inspect_mesh_suite(adapter):
    """Verify all test matrix scenarios for inspect_mesh."""
    tool = InspectMeshTool()

    # 1. Default Cube inspection
    res_cube = tool.execute(adapter, object_name="Cube")
    assert res_cube.success, f"inspect_mesh failed on Cube: {res_cube.error}"
    data = res_cube.data

    assert data["object_name"] == "Cube"
    assert data["mesh_name"] == "Cube"

    # Verify counts
    assert data["counts"]["vertices"] == 8
    assert data["counts"]["edges"] == 12
    assert data["counts"]["polygons"] == 6

    # Verify polygon breakdown
    assert data["polygon_breakdown"]["quads"] == 6
    assert data["polygon_breakdown"]["triangles"] == 0
    assert data["polygon_breakdown"]["ngons"] == 0

    # Verify UV
    assert data["has_uv"] is True
    assert "UVMap" in data["uv_layers"]

    # Verify World Bounding Box
    bbox = data["bounding_box"]
    assert bbox["min"] == [-1.0, -1.0, -1.0]
    assert bbox["max"] == [1.0, 1.0, 1.0]
    assert bbox["center"] == [0.0, 0.0, 0.0]
    print("[PASS] inspect_mesh on default Cube (topology, counts, UV, world bounds) verified.")

    # 2. World-space bounding box translation sanity check
    cube = bpy.data.objects.get("Cube")
    cube.location = (5.0, 10.0, -3.0)
    bpy.context.view_layer.update()

    res_translated = tool.execute(adapter, object_name="Cube")
    assert res_translated.success
    trans_bbox = res_translated.data["bounding_box"]
    assert trans_bbox["min"] == [4.0, 9.0, -4.0]
    assert trans_bbox["max"] == [6.0, 11.0, -2.0]
    assert trans_bbox["center"] == [5.0, 10.0, -3.0]
    print("[PASS] inspect_mesh bounding box is strictly calculated in world coordinate space.")

    # Reset cube location
    cube.location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()

    # 3. Object without mesh (Camera -> INVALID_DATA_TYPE)
    res_cam = tool.execute(adapter, object_name="Camera")
    assert not res_cam.success
    assert res_cam.error.type == "INVALID_DATA_TYPE"
    assert "CAMERA" in res_cam.error.message
    print("[PASS] inspect_mesh on Camera raises INVALID_DATA_TYPE.")

    # 4. Object without mesh (Light -> INVALID_DATA_TYPE)
    res_light = tool.execute(adapter, object_name="Light")
    assert not res_light.success
    assert res_light.error.type == "INVALID_DATA_TYPE"
    print("[PASS] inspect_mesh on Light raises INVALID_DATA_TYPE.")

    # 5. Invalid object name
    res_ghost = tool.execute(adapter, object_name="GhostMeshObject_999")
    assert not res_ghost.success
    assert res_ghost.error.type == "OBJECT_NOT_FOUND"
    print("[PASS] inspect_mesh on non-existent object raises OBJECT_NOT_FOUND.")

    # 6. Mesh without UV
    raw_mesh = bpy.data.meshes.new(name="NoUVMesh")
    raw_obj = bpy.data.objects.new(name="NoUVObject", object_data=raw_mesh)
    bpy.context.collection.objects.link(raw_obj)

    res_no_uv = tool.execute(adapter, object_name="NoUVObject")
    assert res_no_uv.success
    assert res_no_uv.data["has_uv"] is False
    assert res_no_uv.data["uv_layers"] == []
    print("[PASS] inspect_mesh on mesh without UV handles gracefully (has_uv: False).")


if __name__ == "__main__":
    try:
        print("\n=== STARTING PHASE 4 GROUNDING TOOLS HEADLESS INTEGRATION TEST ===")
        test_main_thread_guard()

        adapter = BlenderAdapter()
        # Phase 3 suite
        test_inspect_scene(adapter)
        test_inspect_selection(adapter)
        test_inspect_object_existing(adapter)
        test_inspect_object_without_material(adapter)
        test_inspect_object_not_found(adapter)

        # Phase 4 suite
        test_inspect_material_suite(adapter)
        test_inspect_mesh_suite(adapter)

        print("=== PHASE 4 GROUNDING TOOLS TEST COMPLETED SUCCESSFULLY ===\n")
        sys.exit(0)
    except AssertionError as err:
        print(f"\n[FAIL] Assertion error: {err}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected exception: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
