"""3D Viewport header integration for Blender AI Sidebar."""

import bpy


def draw_viewport_header(self, context):
    """Draw minimal AI launcher button in the 3D Viewport header."""
    props = getattr(context.window_manager, "ai_sidebar", None)
    if not props:
        return

    layout = self.layout
    layout.separator()

    row = layout.row(align=True)
    if props.agent_status in ("PROCESSING", "EXECUTING_TOOL"):
        row.operator("ai_sidebar.viewport_hud", text="✦ AI Busy...", icon="TIME")
        row.operator("ai_sidebar.cancel_turn", text="", icon="CANCEL")
    else:
        row.operator("ai_sidebar.viewport_hud", text="✦ Ask AI", icon="WINDOW")


def register_header():
    """Register header draw callback."""
    try:
        bpy.types.VIEW3D_HT_header.append(draw_viewport_header)
    except Exception:
        pass


def unregister_header():
    """Unregister header draw callback."""
    try:
        bpy.types.VIEW3D_HT_header.remove(draw_viewport_header)
    except Exception:
        pass
