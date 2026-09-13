"""Blender AI Sidebar In-Viewport GPU Overlay Package."""

from .state import overlay_state
from .renderer import draw_overlay_hud
from .modal import register_modal, unregister_modal, AISIDEBAR_OT_viewport_hud


def register():
    register_modal()


def unregister():
    unregister_modal()
