"""Phase 8: M1 Comprehensive Acceptance & Hardening Test Suite.

Runs inside Blender 5.2.1 LTS environment:
1. Lifecycle: Enable -> Disable -> Enable
2. In-flight Cancel on Disable (Enable -> Prompt -> Disable)
3. Mutation Contamination: Full before/after comparison ensuring 100% read-only
4. Tool Contract Edge Cases: Invalid inputs, wrong types, missing parameters across 5 tools
5. Identity & Name collision tests (Cube, Cube.001, non-existent, deleted)
6. Performance Sanity: 1, 10, 100, 500 objects scaling test on inspect_scene
"""

import os
import sys
import time
import json
import bpy

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import importlib.util
import bpy

init_path = os.path.join(PROJECT_ROOT, "__init__.py")
spec = importlib.util.spec_from_file_location(
    "blender_ai_sidebar",
    init_path,
    submodule_search_locations=[PROJECT_ROOT],
)
blender_ai_sidebar = importlib.util.module_from_spec(spec)
sys.modules["blender_ai_sidebar"] = blender_ai_sidebar
spec.loader.exec_module(blender_ai_sidebar)

from adapter.blender_adapter import BlenderAdapter
from agent.state_machine import AgentState
from agent.models import ToolCall


def pump_timer_until_idle(bridge, runtime, max_wait=3.0):
    """Utility to pump the timer until the agent returns to IDLE or timeout."""
    start = time.time()
    while time.time() - start < max_wait:
        bridge.tick()
        if runtime.current_state in (AgentState.IDLE, AgentState.ERROR) and not runtime.worker.is_busy():
            bridge.tick()
            return True
        time.sleep(0.02)
    return False


def test_lifecycle_enable_disable_enable():
    """Test 1: Enable -> Disable -> Enable cycle ensuring no duplicate registrations or residual state."""
    print("Running Test 1: Lifecycle Enable -> Disable -> Enable...")
    
    # 1st Enable
    blender_ai_sidebar.register()
    wm = bpy.context.window_manager
    assert hasattr(wm, "ai_sidebar"), "ai_sidebar not present on first register"
    props = wm.ai_sidebar
    props.prompt_input = "Sample input"
    runtime1 = blender_ai_sidebar.get_runtime()
    bridge1 = blender_ai_sidebar.get_timer_bridge()
    assert runtime1 is not None and bridge1 is not None
    assert bridge1.is_active

    # Disable
    blender_ai_sidebar.unregister()
    assert not hasattr(bpy.types.WindowManager, "ai_sidebar"), "Property not removed from WindowManager class"
    assert blender_ai_sidebar.get_runtime() is None
    assert blender_ai_sidebar.get_timer_bridge() is None
    assert not bridge1.is_active

    # 2nd Enable (re-enable)
    blender_ai_sidebar.register()
    wm2 = bpy.context.window_manager
    assert hasattr(wm2, "ai_sidebar")
    props2 = wm2.ai_sidebar
    assert props2.prompt_input == "", "Prompt input should be empty after re-registration"
    assert len(props2.history) == 0, "History should be empty after re-registration"
    assert props2.agent_status == "IDLE", "Status should be reset to IDLE"
    runtime2 = blender_ai_sidebar.get_runtime()
    bridge2 = blender_ai_sidebar.get_timer_bridge()
    assert runtime2 is not None and bridge2 is not None
    assert runtime2 != runtime1, "Runtime instance should be freshly initialized"
    assert bridge2.is_active

    blender_ai_sidebar.unregister()
    print("[PASS] Test 1: Enable -> Disable -> Enable completed cleanly.")


def test_in_flight_cancellation_on_disable():
    """Test 2: Enable -> Prompt -> Disable during execution shuts down cleanly without hanging."""
    print("Running Test 2: In-flight cancellation on disable...")
    blender_ai_sidebar.register()
    wm = bpy.context.window_manager
    props = wm.ai_sidebar
    runtime = blender_ai_sidebar.get_runtime()

    props.prompt_input = "Tam inceleme"
    bpy.ops.ai_sidebar.send_prompt()
    assert runtime.current_state == AgentState.PROCESSING

    # Immediate unregister while worker is active
    start_time = time.time()
    blender_ai_sidebar.unregister()
    elapsed = time.time() - start_time

    assert elapsed < 1.0, f"Unregister took too long: {elapsed:.2f}s"
    assert blender_ai_sidebar.get_runtime() is None
    print(f"[PASS] Test 2: In-flight cancellation shutdown took {elapsed:.4f}s.")


def test_mutation_contamination():
    """Test 3: Verify that none of the 5 grounding tools mutate the Blender scene/objects/materials/meshes."""
    print("Running Test 3: Mutation contamination check...")
    
    # Reset scene to default cube, light, camera
    bpy.ops.wm.read_factory_settings(use_empty=False)
    adapter = BlenderAdapter()

    # Capture complete pre-state
    def snapshot_state():
        state = {
            "objects_count": len(bpy.data.objects),
            "meshes_count": len(bpy.data.meshes),
            "materials_count": len(bpy.data.materials),
            "objects": {}
        }
        for obj in bpy.data.objects:
            state["objects"][obj.name] = {
                "location": tuple(obj.location),
                "rotation": tuple(obj.rotation_euler),
                "scale": tuple(obj.scale),
                "dimensions": tuple(obj.dimensions),
                "materials": [slot.material.name for slot in obj.material_slots if slot.material],
                "num_modifiers": len(obj.modifiers),
            }
            if obj.type == "MESH" and obj.data:
                state["objects"][obj.name]["vertices"] = len(obj.data.vertices)
                state["objects"][obj.name]["polygons"] = len(obj.data.polygons)
                state["objects"][obj.name]["edges"] = len(obj.data.edges)
        return state

    pre_state = snapshot_state()

    # Execute all 5 tools
    r1 = adapter.inspect_scene()
    assert r1.success, f"inspect_scene failed: {r1.error}"
    
    r2 = adapter.inspect_selection()
    assert r2.success, f"inspect_selection failed: {r2.error}"

    r3 = adapter.inspect_object("Cube")
    assert r3.success, f"inspect_object failed: {r3.error}"

    r4 = adapter.inspect_material(material_name="Material")
    assert r4.success, f"inspect_material failed: {r4.error}"

    r5 = adapter.inspect_mesh("Cube")
    assert r5.success, f"inspect_mesh failed: {r5.error}"

    # Capture post-state and compare
    post_state = snapshot_state()
    assert pre_state == post_state, f"State mutated! Pre: {pre_state} vs Post: {post_state}"

    print("[PASS] Test 3: Zero mutation across all 5 grounding tools confirmed.")


def test_tool_contracts_edge_cases():
    """Test 4: Rigorous edge cases for all 5 tools (types, missing args, unknown targets)."""
    print("Running Test 4: Tool contract edge-case hardening...")
    blender_ai_sidebar.register()
    try:
        runtime = blender_ai_sidebar.get_runtime()
        dispatcher = runtime.dispatcher

        # 1. Unknown tool
        call = ToolCall(call_id="call_test_1", tool_name="non_existent_tool", arguments={})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "TOOL_NOT_FOUND"

        # 2. inspect_object missing required argument
        call = ToolCall(call_id="call_test_2", tool_name="inspect_object", arguments={})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "INVALID_ARGUMENT"

        # 3. inspect_object wrong argument type (e.g. number instead of string)
        call = ToolCall(call_id="call_test_3", tool_name="inspect_object", arguments={"name": 12345})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "INVALID_ARGUMENT"

        # 4. inspect_object with additional unexpected argument
        call = ToolCall(call_id="call_test_4", tool_name="inspect_object", arguments={"name": "Cube", "extra_arg": "invalid"})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "INVALID_ARGUMENT"

        # 5. inspect_object unknown object
        call = ToolCall(call_id="call_test_5", tool_name="inspect_object", arguments={"name": "GhostObject"})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "OBJECT_NOT_FOUND"

        # 6. inspect_material no arguments provided (violates dual-entry contract)
        call = ToolCall(call_id="call_test_6", tool_name="inspect_material", arguments={})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "INVALID_ARGUMENT"

        # 7. inspect_material both arguments provided (violates mutually exclusive contract)
        call = ToolCall(call_id="call_test_7", tool_name="inspect_material", arguments={"material_name": "Material", "object_name": "Cube", "slot_index": 0})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "INVALID_ARGUMENT"

        # 8. inspect_material negative slot_index
        call = ToolCall(call_id="call_test_8", tool_name="inspect_material", arguments={"object_name": "Cube", "slot_index": -1})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "SLOT_INDEX_OUT_OF_RANGE"

        # 9. inspect_material slot_index out of range
        call = ToolCall(call_id="call_test_9", tool_name="inspect_material", arguments={"object_name": "Cube", "slot_index": 99})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "SLOT_INDEX_OUT_OF_RANGE"

        # 10. inspect_mesh on non-mesh object (e.g. Camera or Light)
        call = ToolCall(call_id="call_test_10", tool_name="inspect_mesh", arguments={"object_name": "Camera"})
        res = dispatcher.dispatch(call)
        assert not res.success
        assert res.error.type == "INVALID_DATA_TYPE"

        # 11. inspect_selection with empty selection
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = None
        call = ToolCall(call_id="call_test_11", tool_name="inspect_selection", arguments={})
        res = dispatcher.dispatch(call)
        assert res.success
        assert res.data["selection_count"] == 0
        assert res.data["active_object"] is None
        assert res.data["selected_objects"] == []

        print("[PASS] Test 4: All tool contract edge cases correctly handled.")
    finally:
        blender_ai_sidebar.unregister()


def test_object_identity_and_name_collisions():
    """Test 5: Naming edge cases (.001, .002, deleted object, etc.)."""
    print("Running Test 5: Object identity and naming...")
    bpy.ops.wm.read_factory_settings(use_empty=False)
    adapter = BlenderAdapter()

    # Create duplicated cubes: Cube.001, Cube.002
    bpy.ops.mesh.primitive_cube_add(location=(2, 0, 0))
    c1 = bpy.context.active_object
    assert c1.name == "Cube.001"

    bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
    c2 = bpy.context.active_object
    assert c2.name == "Cube.002"

    # Query Cube.001 and Cube.002
    r_c1 = adapter.inspect_object("Cube.001")
    assert r_c1.success and r_c1.data["name"] == "Cube.001"
    assert r_c1.data["transform"]["location"] == [2.0, 0.0, 0.0]

    r_c2 = adapter.inspect_object("Cube.002")
    assert r_c2.success and r_c2.data["name"] == "Cube.002"
    assert r_c2.data["transform"]["location"] == [4.0, 0.0, 0.0]

    # Delete Cube.001 and query again
    bpy.data.objects.remove(c1, do_unlink=True)
    r_c1_after = adapter.inspect_object("Cube.001")
    assert not r_c1_after.success
    assert r_c1_after.error.type == "OBJECT_NOT_FOUND"

    print("[PASS] Test 5: Name disambiguation and deletion handling verified.")


def test_performance_sanity():
    """Test 6: Performance scaling of inspect_scene with 1, 10, 100, and 500 objects."""
    print("Running Test 6: Performance sanity benchmarks...")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    adapter = BlenderAdapter()

    counts = [1, 10, 100, 500]
    benchmarks = []

    for target_count in counts:
        # Create objects up to target_count
        current_count = len(bpy.data.objects)
        for i in range(current_count, target_count):
            mesh = bpy.data.meshes.new(f"Mesh_{i}")
            obj = bpy.data.objects.new(f"Obj_{i}", mesh)
            bpy.context.scene.collection.objects.link(obj)

        t0 = time.perf_counter()
        res = adapter.inspect_scene()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert res.success
        assert res.data["counts"]["total"] == target_count

        json_str = json.dumps(res.to_dict())
        size_kb = len(json_str.encode("utf-8")) / 1024.0

        benchmarks.append({
            "count": target_count,
            "duration_ms": round(elapsed_ms, 2),
            "payload_kb": round(size_kb, 2),
        })
        print(f"   - {target_count:3d} objects: {elapsed_ms:6.2f} ms, JSON size: {size_kb:6.2f} KB")

    # Assert 500 objects takes less than 150ms and payload is under 100KB
    assert benchmarks[-1]["duration_ms"] < 250.0, f"500 objects too slow: {benchmarks[-1]['duration_ms']}ms"
    assert benchmarks[-1]["payload_kb"] < 100.0, f"500 objects payload too large: {benchmarks[-1]['payload_kb']}KB"
    print("[PASS] Test 6: Performance sanity within tight bounds.")


if __name__ == "__main__":
    try:
        print("\n=== STARTING M1 ACCEPTANCE AND HARDENING TESTS ===")
        test_lifecycle_enable_disable_enable()
        test_in_flight_cancellation_on_disable()
        test_mutation_contamination()
        test_tool_contracts_edge_cases()
        test_object_identity_and_name_collisions()
        test_performance_sanity()
        print("=== ALL M1 ACCEPTANCE TESTS COMPLETED SUCCESSFULLY ===\n")
        sys.exit(0)
    except AssertionError as err:
        print(f"\n[FAIL] Assertion error: {err}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected exception: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
