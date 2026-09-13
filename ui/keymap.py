"""Keymap registration for Blender AI Sidebar shortcut actions."""

import bpy
from typing import List, Tuple

_keymaps: List[Tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def register_keymaps():
    """Register Alt+Space and Shift+Alt+A shortcuts to launch in-viewport GPU HUD."""
    wm = bpy.context.window_manager
    if not wm or not wm.keyconfigs or not wm.keyconfigs.addon:
        return

    kc = wm.keyconfigs.addon
    km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")

    # Primary shortcut: Alt + Space -> Open In-Viewport GPU HUD
    kmi = km.keymap_items.new(
        "ai_sidebar.viewport_hud",
        type="SPACE",
        value="PRESS",
        alt=True,
    )
    _keymaps.append((km, kmi))

    # Secondary shortcut: Shift + Alt + A -> Open In-Viewport GPU HUD
    kmi2 = km.keymap_items.new(
        "ai_sidebar.viewport_hud",
        type="A",
        value="PRESS",
        shift=True,
        alt=True,
    )
    _keymaps.append((km, kmi2))


def unregister_keymaps():
    """Unregister and clean up all added shortcut items."""
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ValueError, KeyError, RuntimeError):
            pass
    _keymaps.clear()
