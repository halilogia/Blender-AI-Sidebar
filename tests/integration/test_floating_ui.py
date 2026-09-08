"""Headless integration tests for Floating AI Command Bar & Conversation Drawer."""

import os
import sys
import time
import importlib.util
import bpy

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

init_path = os.path.join(PROJECT_ROOT, "__init__.py")
spec = importlib.util.spec_from_file_location(
    "blender_ai_sidebar",
    init_path,
    submodule_search_locations=[PROJECT_ROOT],
)
blender_ai_sidebar = importlib.util.module_from_spec(spec)
sys.modules["blender_ai_sidebar"] = blender_ai_sidebar
spec.loader.exec_module(blender_ai_sidebar)

from ui.command_bar import AISIDEBAR_PT_command_bar
from ui.conversation_drawer import AISIDEBAR_PT_conversation_drawer
from ui.panel import AISIDEBAR_PT_main_panel
from ui.timer_bridge import TimerBridge
from agent.state_machine import AgentState


def pump_timer_until_idle(bridge: TimerBridge, runtime, timeout: float = 2.0) -> bool:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        bridge.tick()
        if runtime.current_state == AgentState.IDLE and runtime.current_turn_id is None:
            return True
        time.sleep(0.01)
    return False


def test_floating_ui_registration():
    """Test 1: Floating UI panels and operators registration."""
    print("Running Test 1: Floating UI registration...")
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        assert hasattr(wm, "ai_sidebar"), "wm has no ai_sidebar property"
        props = wm.ai_sidebar

        # Check properties
        assert hasattr(props, "ui_mode"), "props has no ui_mode"
        assert hasattr(props, "live_streaming_text"), "props has no live_streaming_text"
        assert props.ui_mode == "CLOSED", f"Expected CLOSED, got {props.ui_mode}"

        # Check operator availability
        assert hasattr(bpy.ops.ai_sidebar, "open_command_bar"), "Missing open_command_bar"
        assert hasattr(bpy.ops.ai_sidebar, "open_conversation"), "Missing open_conversation"
        assert hasattr(bpy.ops.ai_sidebar, "send_prompt"), "Missing send_prompt"
        assert hasattr(bpy.ops.ai_sidebar, "cancel_turn"), "Missing cancel_turn"

        print("[PASS] Test 1: Floating UI classes, properties and operators registered cleanly.")
    finally:
        blender_ai_sidebar.unregister()


def test_keymap_registration():
    """Test 2: Keymap registration for 3D View."""
    print("Running Test 2: Keymap registration...")
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon
        assert kc is not None, "No addon keyconfig found"

        km = kc.keymaps.get("3D View")
        assert km is not None, "3D View keymap not found"

        found_shortcuts = []
        for kmi in km.keymap_items:
            if kmi.idname == "ai_sidebar.open_command_bar":
                found_shortcuts.append((kmi.type, kmi.alt, kmi.shift))

        assert len(found_shortcuts) >= 2, f"Expected at least 2 shortcuts, found: {found_shortcuts}"
        assert ("SPACE", True, False) in found_shortcuts, "Alt+Space not registered!"
        assert ("A", True, True) in found_shortcuts, "Shift+Alt+A not registered!"

        print("[PASS] Test 2: Alt+Space and Shift+Alt+A keymaps successfully registered.")
    finally:
        blender_ai_sidebar.unregister()


def test_ui_mode_transitions_and_flow():
    """Test 3: State transitions from COMMAND_BAR to CONVERSATION."""
    print("Running Test 3: UI mode transitions and execution flow...")
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        props = wm.ai_sidebar
        runtime = blender_ai_sidebar.get_runtime()
        bridge = blender_ai_sidebar.get_timer_bridge()

        assert runtime is not None
        assert bridge is not None

        # 1. Open Command Bar
        bpy.ops.ai_sidebar.open_command_bar()
        assert props.ui_mode == "COMMAND_BAR", f"Expected COMMAND_BAR, got {props.ui_mode}"

        # 2. Enter prompt and send
        props.prompt_input = "Sahneyi incele"
        bpy.ops.ai_sidebar.send_prompt()

        # Should immediately switch ui_mode to CONVERSATION
        assert props.ui_mode == "CONVERSATION", f"Expected CONVERSATION, got {props.ui_mode}"
        assert props.prompt_input == "", "Prompt input should be cleared upon send"

        # Pump timer until execution finishes
        success = pump_timer_until_idle(bridge, runtime, timeout=2.0)
        assert success, "Agent did not finish within timeout"

        # Verify history has entries
        assert len(props.history) >= 2, f"Expected at least 2 history items, got {len(props.history)}"
        user_items = [it for it in props.history if it.kind == "USER"]
        assert len(user_items) == 1, "Expected 1 USER history item"
        assert user_items[0].summary == "Sahneyi incele"

        print("[PASS] Test 3: Flow from Command Bar -> Prompt Send -> Conversation Drawer verified.")
    finally:
        blender_ai_sidebar.unregister()


def test_drawing_hardening():
    """Test 4: Verify panels draw without throwing exceptions under real context."""
    print("Running Test 4: Panel draw hardening...")
    blender_ai_sidebar.register()
    try:
        class MockLayout:
            def __init__(self):
                self.labels = []
                self.scale_y = 1.0
                self.alert = False
            def column(self, align=False):
                return self
            def row(self, align=False):
                return self
            def box(self):
                return self
            def separator(self):
                pass
            def label(self, text="", icon="NONE"):
                self.labels.append((text, icon))
            def prop(self, data, prop_name, **kwargs):
                pass
            def operator(self, op_name, **kwargs):
                return self
            def template_list(self, *args, **kwargs):
                pass

        class DummyPanel:
            def __init__(self):
                self.layout = MockLayout()

        # 1. Test Command Bar draw
        cb = DummyPanel()
        AISIDEBAR_PT_command_bar.draw(cb, bpy.context)

        # 2. Test Conversation Drawer draw
        cd = DummyPanel()
        AISIDEBAR_PT_conversation_drawer.draw(cd, bpy.context)

        # 3. Test Inspector N-Panel draw
        np = DummyPanel()
        AISIDEBAR_PT_main_panel.draw(np, bpy.context)

        print("[PASS] Test 4: All 3 panels draw cleanly without errors.")
    finally:
        blender_ai_sidebar.unregister()


def run_all():
    print("\n========================================================")
    print("   RUNNING FLOATING UI INTEGRATION SUITE (BLENDER 5.2)  ")
    print("========================================================\n")

    test_floating_ui_registration()
    test_keymap_registration()
    test_ui_mode_transitions_and_flow()
    test_drawing_hardening()

    print("\n========================================================")
    print("   ALL FLOATING UI TESTS PASSED SUCCESSFULLY!           ")
    print("========================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
