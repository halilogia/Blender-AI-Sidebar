"""Headless integration tests for Blender AI Viewport GPU Overlay (Higgsfield Style)."""

import os
import sys
import importlib.util
import bpy
import gpu

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

from ui.gpu_overlay.state import GPUOverlayState, overlay_state
from ui.gpu_overlay.renderer import (
    build_rounded_rect_tris,
    draw_rounded_rect,
    draw_rounded_shadow,
)


def test_registration():
    """Test 1: Operator and keymap registration for viewport HUD."""
    print("Running Test 1: Operator and keymap registration...")
    blender_ai_sidebar.register()
    try:
        # Check operator
        assert hasattr(bpy.ops.ai_sidebar, "viewport_hud"), "Missing ai_sidebar.viewport_hud operator"

        # Check keymaps
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon
        assert kc is not None
        km = kc.keymaps.get("3D View")
        assert km is not None

        found_shortcuts = []
        for kmi in km.keymap_items:
            if kmi.idname == "ai_sidebar.viewport_hud":
                found_shortcuts.append((kmi.type, kmi.alt, kmi.shift))

        assert len(found_shortcuts) >= 2, f"Expected 2 shortcuts, found: {found_shortcuts}"
        assert ("SPACE", True, False) in found_shortcuts, "Alt+Space not registered to viewport_hud"
        assert ("A", True, True) in found_shortcuts, "Shift+Alt+A not registered to viewport_hud"

        print("[PASS] Test 1: Operator and keymaps registered cleanly.")
    finally:
        blender_ai_sidebar.unregister()


def test_overlay_state_text_and_unicode():
    """Test 2: Overlay state text editing and Turkish unicode support."""
    print("Running Test 2: Overlay state text and unicode...")
    state = GPUOverlayState()

    # 1. Insertion
    state.insert_text("Merhaba")
    assert state.prompt_text == "Merhaba"
    assert state.cursor_pos == 7

    # 2. Turkish characters
    state.insert_text(" dünya! ğüşiöç ĞÜŞİÖÇ")
    assert "ğüşiöç" in state.prompt_text
    assert "ĞÜŞİÖÇ" in state.prompt_text

    # 3. Cursor navigation
    state.move_cursor_home()
    assert state.cursor_pos == 0
    state.move_cursor_right()
    assert state.cursor_pos == 1
    state.move_cursor_end()
    assert state.cursor_pos == len(state.prompt_text)

    # 4. Deletions
    state.delete_backward()
    assert not state.prompt_text.endswith("Ç")
    state.move_cursor_home()
    state.delete_forward()
    assert not state.prompt_text.startswith("M")

    # 5. Reset
    state.reset_input()
    assert state.prompt_text == ""
    assert state.cursor_pos == 0

    print("[PASS] Test 2: State text buffer and Turkish unicode handling verified.")


def test_hit_testing():
    """Test 3: Hit testing for bar, buttons, and input areas."""
    print("Running Test 3: Hit testing...")
    state = GPUOverlayState()
    state.bar_rect = (100.0, 50.0, 500.0, 70.0)
    state.input_rect = (140.0, 80.0, 320.0, 30.0)
    state.send_btn_rect = (480.0, 60.0, 100.0, 44.0)

    # Point outside
    assert state.hit_test(50.0, 50.0) is None
    assert state.hit_test(100.0, 20.0) is None

    # Point inside bar background
    assert state.hit_test(110.0, 55.0) == "bar"

    # Point inside input
    assert state.hit_test(160.0, 90.0) == "input"

    # Point inside send button
    assert state.hit_test(500.0, 75.0) == "send"

    print("[PASS] Test 3: Hit testing verified.")


def test_gpu_geometry_and_drawing():
    """Test 4: GPU rounded rectangle tessellation and drawing safety."""
    print("Running Test 4: GPU geometry and drawing...")
    gpu.init()

    # 1. Tessellation test
    tris = build_rounded_rect_tris(100, 100, 400, 70, radius=18.0)
    assert len(tris) > 20, "Tessellation should generate multiple triangles"
    for pt in tris:
        assert len(pt) == 2

    # 2. Drawing execution (must not raise exceptions)
    gpu.state.blend_set("ALPHA")
    draw_rounded_shadow(100, 100, 400, 70, radius=18.0)
    draw_rounded_rect(100, 100, 400, 70, radius=18.0, color=(0.1, 0.1, 0.12, 1.0))
    gpu.state.blend_set("NONE")

    print("[PASS] Test 4: GPU geometry tessellation and drawing verified.")


def run_all():
    print("\n========================================================")
    print("   RUNNING GPU OVERLAY INTEGRATION SUITE (BLENDER 5.2)  ")
    print("========================================================\n")

    test_registration()
    test_overlay_state_text_and_unicode()
    test_hit_testing()
    test_gpu_geometry_and_drawing()

    print("\n========================================================")
    print("   ALL GPU OVERLAY TESTS PASSED SUCCESSFULLY!           ")
    print("========================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
