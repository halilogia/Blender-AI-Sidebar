"""Headless integration tests for Phase 7 UI Integration on Blender 5.2.1 LTS.

Tests:
1. Registration/unregistration of UI properties, operators, uilist, and panels.
2. Prompt submission via operator (ai_sidebar.send_prompt).
3. Synchronization of RuntimeHistory to UIList CollectionProperty.
4. History item selection and detail formatting.
5. In-flight cancellation via operator (ai_sidebar.cancel_turn).
6. Clear history via operator (ai_sidebar.clear_history).
7. Error state representation in UI properties.
8. Multi-window / 3D Viewport tag_redraw resilience.
"""

import os
import sys
import time
import importlib.util

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["BLENDER_AI_USE_MOCK_PROVIDER"] = "1"

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

from agent.state_machine import AgentState
from ui.timer_bridge import TimerBridge


def pump_timer_until_idle(bridge: TimerBridge, runtime, timeout: float = 2.0) -> bool:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        bridge.tick()
        if runtime.current_state == AgentState.IDLE and runtime.current_turn_id is None:
            return True
        time.sleep(0.01)
    return False


def test_registration_and_properties():
    """Test 1: Verify all UI classes are registered and properties accessible on WindowManager."""
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        assert hasattr(wm, "ai_sidebar"), "WindowManager has no ai_sidebar property"
        props = wm.ai_sidebar
        assert hasattr(props, "prompt_input")
        assert hasattr(props, "agent_status")
        assert hasattr(props, "current_action")
        assert hasattr(props, "history")
        assert hasattr(props, "history_index")

        # Check operator registration
        assert hasattr(bpy.ops.ai_sidebar, "send_prompt")
        assert hasattr(bpy.ops.ai_sidebar, "cancel_turn")
        assert hasattr(bpy.ops.ai_sidebar, "clear_history")

        # Check panel registration
        assert hasattr(bpy.types, "AISIDEBAR_PT_main_panel")
        print("[PASS] UI Registration and properties verified.")
    finally:
        blender_ai_sidebar.unregister()


def test_send_prompt_operator_and_history_sync():
    """Test 2: Test send_prompt operator, execution, and history sync into UIList."""
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        props = wm.ai_sidebar
        runtime = blender_ai_sidebar.get_runtime()
        bridge = blender_ai_sidebar.get_timer_bridge()

        # Set prompt and invoke operator
        props.prompt_input = "Mevcut sahneyi incele"
        res = bpy.ops.ai_sidebar.send_prompt()
        assert res == {"FINISHED"}, f"Send prompt failed: {res}"
        assert props.prompt_input == "", "Prompt input should be cleared after submit"

        # Pump timer
        done = pump_timer_until_idle(bridge, runtime)
        assert done, "Turn did not complete within timeout"

        # Check UI properties updated
        assert props.agent_status == "IDLE"
        assert props.current_action == "Ready"
        assert len(props.history) >= 3  # USER, TOOL, ASSISTANT

        user_item = props.history[0]
        assert user_item.kind == "USER"
        assert "Mevcut sahneyi incele" in user_item.title

        tool_item = props.history[1]
        assert tool_item.kind == "TOOL"
        assert "inspect_scene" in tool_item.title
        assert tool_item.status == "OK"

        asst_item = props.history[2]
        assert asst_item.kind == "ASSISTANT"
        assert asst_item.status == "DONE"
        assert "Sahne incelemesi" in asst_item.summary

        print("[PASS] Send prompt operator and history sync verified.")
    finally:
        blender_ai_sidebar.unregister()


def test_clear_history_operator():
    """Test 3: Test clear_history operator."""
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        props = wm.ai_sidebar
        runtime = blender_ai_sidebar.get_runtime()
        bridge = blender_ai_sidebar.get_timer_bridge()

        props.prompt_input = "Mevcut sahneyi incele"
        bpy.ops.ai_sidebar.send_prompt()
        pump_timer_until_idle(bridge, runtime)

        assert len(props.history) > 0
        assert len(runtime.history) > 0

        # Execute clear
        res = bpy.ops.ai_sidebar.clear_history()
        assert res == {"FINISHED"}
        assert len(props.history) == 0
        assert len(runtime.history) == 0
        assert props.history_index == -1
        print("[PASS] Clear history operator verified.")
    finally:
        blender_ai_sidebar.unregister()


def test_cancel_operator():
    """Test 4: Test cancel_turn operator."""
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        props = wm.ai_sidebar
        runtime = blender_ai_sidebar.get_runtime()
        bridge = blender_ai_sidebar.get_timer_bridge()

        props.prompt_input = "Tam inceleme"
        bpy.ops.ai_sidebar.send_prompt()

        # Immediately cancel via operator
        res = bpy.ops.ai_sidebar.cancel_turn()
        assert res == {"FINISHED"}
        assert runtime.current_turn_id is None

        # Give worker a moment to finish any thread work
        time.sleep(0.05)
        bridge.tick()

        # Last item in history should record cancellation or user prompt
        assert props.agent_status == "IDLE"
        print("[PASS] Cancel operator verified.")
    finally:
        blender_ai_sidebar.unregister()


def test_error_handling_in_ui():
    """Test 5: Verify error state and message flow to UI on unknown prompt."""
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        props = wm.ai_sidebar
        runtime = blender_ai_sidebar.get_runtime()
        bridge = blender_ai_sidebar.get_timer_bridge()

        # Unknown prompt that yields direct response without error
        props.prompt_input = "Bilinmeyen komut"
        bpy.ops.ai_sidebar.send_prompt()
        pump_timer_until_idle(bridge, runtime)

        assert len(props.history) == 2  # USER + ASSISTANT
        assert "Anlaşılmayan" in props.history[1].summary
        print("[PASS] Error / unknown intent handling in UI verified.")
    finally:
        blender_ai_sidebar.unregister()


def test_multiple_view3d_redraw_safe():
    """Test 6: Verify redraw method is safe in headless / multi-window environment."""
    # Should not raise any exception
    TimerBridge.tag_redraw_view3d()
    print("[PASS] Tag redraw safety verified.")


def test_operator_poll_and_selection():
    """Test 7: Verify operator poll rules and history item details lookup."""
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        props = wm.ai_sidebar
        runtime = blender_ai_sidebar.get_runtime()
        bridge = blender_ai_sidebar.get_timer_bridge()

        # When prompt_input is empty, send poll should fail
        props.prompt_input = ""
        assert not bpy.ops.ai_sidebar.send_prompt.poll()

        # When prompt_input is set, send poll should pass
        props.prompt_input = "Cube'u incele"
        assert bpy.ops.ai_sidebar.send_prompt.poll()

        # Send prompt
        bpy.ops.ai_sidebar.send_prompt()
        pump_timer_until_idle(bridge, runtime)

        # Check history items count and details
        assert len(props.history) >= 3
        # Select first item (User prompt)
        props.history_index = 0
        first_item = runtime.history.get_by_index(0)
        assert first_item is not None
        assert first_item.kind == "USER"
        assert first_item.detail == "Cube'u incele"

        # Select second item (Tool)
        props.history_index = 1
        second_item = runtime.history.get_by_index(1)
        assert second_item is not None
        assert second_item.kind == "TOOL"
        assert "inspect_object" in second_item.detail

        print("[PASS] Operator poll rules and history navigation verified.")
    finally:
        blender_ai_sidebar.unregister()


if __name__ == "__main__":
    try:
        print("\n=== STARTING PHASE 7 UI INTEGRATION TESTS ===")
        test_registration_and_properties()
        test_send_prompt_operator_and_history_sync()
        test_clear_history_operator()
        test_cancel_operator()
        test_error_handling_in_ui()
        test_multiple_view3d_redraw_safe()
        test_operator_poll_and_selection()

        print("=== PHASE 7 UI INTEGRATION TESTS COMPLETED SUCCESSFULLY ===\n")
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
