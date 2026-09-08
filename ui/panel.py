"""3D Viewport N-Panel minimal launcher for Blender AI Sidebar."""

import bpy
from bpy.types import Panel


class AISIDEBAR_PT_main_panel(Panel):
    """Minimal launcher & status card located in the 3D Viewport Sidebar (N-Panel)."""

    bl_label = "Blender AI Copilot"
    bl_idname = "AISIDEBAR_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Sidebar"

    def draw(self, context):
        layout = self.layout
        props = getattr(context.window_manager, "ai_sidebar", None)

        if not props:
            layout.label(text="AI Sidebar properties not initialized.", icon="ERROR")
            return

        # ---------------------------------------------------------------------
        # 1. Primary Action: Launch Modern Web UI
        # ---------------------------------------------------------------------
        col = layout.column(align=True)
        col.scale_y = 1.4
        col.operator("ai_sidebar.open_web_ui", text="✦ Open Web AI", icon="WINDOW")

        hint_row = layout.row(align=True)
        hint_row.scale_y = 0.85
        hint_row.label(text="Shortcut: Alt + Space", icon="INFO")

        layout.separator()

        # ---------------------------------------------------------------------
        # 2. Agent & Bridge Status
        # ---------------------------------------------------------------------
        box = layout.box()
        status = props.agent_status

        if status == "IDLE":
            box.label(text="Bridge: Online (Ready)", icon="CHECKMARK")
        elif status in ("PROCESSING", "EXECUTING_TOOL"):
            box.label(text=f"AI: {props.current_action}", icon="TIME")
            box.operator("ai_sidebar.cancel_turn", text="Cancel Turn", icon="CANCEL")
        elif status == "ERROR":
            box.label(text="AI: Error encountered", icon="ERROR")
        else:
            box.label(text=f"Status: {status}", icon="INFO")


CLASSES = (
    AISIDEBAR_PT_main_panel,
)


def register_panels():
    """Register panel classes."""
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_panels():
    """Unregister panel classes."""
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError):
            pass
