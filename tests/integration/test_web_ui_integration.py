"""Headless integration tests for Modern Web UI Launcher and Local Web Bridge."""

import os
import sys
import time
import json
import urllib.request
import urllib.error
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

from ui.panel import AISIDEBAR_PT_main_panel
from ui.header import draw_viewport_header
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


def test_web_ui_registration():
    """Test 1: Web UI operators and panel registration."""
    print("Running Test 1: Web UI registration...")
    blender_ai_sidebar.register()
    try:
        wm = bpy.context.window_manager
        assert hasattr(wm, "ai_sidebar"), "wm has no ai_sidebar property"
        props = wm.ai_sidebar

        # Check operator availability
        assert hasattr(bpy.ops.ai_sidebar, "open_web_ui"), "Missing open_web_ui operator"
        assert hasattr(bpy.ops.ai_sidebar, "send_prompt"), "Missing send_prompt operator"
        assert hasattr(bpy.ops.ai_sidebar, "cancel_turn"), "Missing cancel_turn operator"
        assert hasattr(bpy.ops.ai_sidebar, "clear_history"), "Missing clear_history operator"

        # Check panel registration
        assert hasattr(bpy.types, "AISIDEBAR_PT_main_panel"), "Missing AISIDEBAR_PT_main_panel"

        print("[PASS] Test 1: Web UI operators, properties, and panel registered cleanly.")
    finally:
        blender_ai_sidebar.unregister()


def test_keymap_registration():
    """Test 2: Keymap registration for Web UI shortcuts."""
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
            if kmi.idname == "ai_sidebar.open_web_ui":
                found_shortcuts.append((kmi.type, kmi.alt, kmi.shift))

        assert len(found_shortcuts) >= 2, f"Expected at least 2 shortcuts, found: {found_shortcuts}"
        assert ("SPACE", True, False) in found_shortcuts, "Alt+Space not registered to open_web_ui!"
        assert ("A", True, True) in found_shortcuts, "Shift+Alt+A not registered to open_web_ui!"

        print("[PASS] Test 2: Alt+Space and Shift+Alt+A correctly bound to open_web_ui.")
    finally:
        blender_ai_sidebar.unregister()


def test_web_server_bridge():
    """Test 3: Web server bridge starts, serves status, and authenticates."""
    print("Running Test 3: Local web server bridge...")
    blender_ai_sidebar.register()
    try:
        server = blender_ai_sidebar.get_web_server()
        assert server is not None, "Web server not initialized"
        assert server.is_running, "Web server is not running"
        assert server.port > 0, f"Invalid port: {server.port}"
        assert len(server.token) == 32, "Invalid token length"

        url = f"http://127.0.0.1:{server.port}/api/status?token={server.token}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("agent_status") == "IDLE"

        # Unauthorized request check
        bad_url = f"http://127.0.0.1:{server.port}/api/status"
        try:
            urllib.request.urlopen(bad_url, timeout=2.0)
            assert False, "Should have raised HTTPError 403"
        except urllib.error.HTTPError as err:
            assert err.code == 403

        print("[PASS] Test 3: Local web server bridge and token authentication verified.")
    finally:
        blender_ai_sidebar.unregister()
        server = blender_ai_sidebar.get_web_server()
        assert server is None or not server.is_running, "Server should be stopped after unregister"


def test_drawing_hardening():
    """Test 4: Verify N-Panel and Header draw without throwing exceptions."""
    print("Running Test 4: Panel and Header draw hardening...")
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

        class DummyPanel:
            def __init__(self):
                self.layout = MockLayout()

        # 1. Test N-Panel draw
        panel = DummyPanel()
        AISIDEBAR_PT_main_panel.draw(panel, bpy.context)

        # 2. Test 3D Viewport Header draw
        header = DummyPanel()
        draw_viewport_header(header, bpy.context)

        print("[PASS] Test 4: N-Panel and Header draw cleanly without errors.")
    finally:
        blender_ai_sidebar.unregister()


def test_web_prompt_dispatch():
    """Test 5: Submit prompt via HTTP bridge and verify end-to-end execution."""
    print("Running Test 5: Web prompt submission flow...")
    blender_ai_sidebar.register()
    try:
        server = blender_ai_sidebar.get_web_server()
        runtime = blender_ai_sidebar.get_runtime()
        bridge = blender_ai_sidebar.get_timer_bridge()

        url = f"http://127.0.0.1:{server.port}/api/prompt?token={server.token}"
        payload = json.dumps({"prompt": "Sahneyi incele"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

        with urllib.request.urlopen(req, timeout=2.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("status") == "ok"

        # Pump timer until execution finishes
        success = pump_timer_until_idle(bridge, runtime, timeout=2.0)
        assert success, "Agent did not finish within timeout"

        # Verify history has entries
        wm = bpy.context.window_manager
        props = wm.ai_sidebar
        assert len(props.history) >= 2, f"Expected at least 2 history items, got {len(props.history)}"

        print("[PASS] Test 5: Web prompt submission and execution verified.")
    finally:
        blender_ai_sidebar.unregister()


def run_all():
    print("\n========================================================")
    print("   RUNNING WEB UI INTEGRATION SUITE (BLENDER 5.2)       ")
    print("========================================================\n")

    test_web_ui_registration()
    test_keymap_registration()
    test_web_server_bridge()
    test_drawing_hardening()
    test_web_prompt_dispatch()

    print("\n========================================================")
    print("   ALL WEB UI INTEGRATION TESTS PASSED SUCCESSFULLY!    ")
    print("========================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
