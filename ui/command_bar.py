"""Floating AI Command Bar for Blender 3D Viewport."""

import bpy
from bpy.types import Panel


class AISIDEBAR_PT_command_bar(Panel):
    """Minimal floating command input bar for Blender AI Copilot."""

    bl_label = "✦ Blender AI"
    bl_idname = "AISIDEBAR_PT_command_bar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_ui_units_x = 22

    def draw(self, context):
        layout = self.layout
        props = getattr(context.window_manager, "ai_sidebar", None)
        if not props:
            layout.label(text="AI Sidebar properties not initialized.", icon="ERROR")
            return

        # Main Command Input Container
        col = layout.column(align=True)

        input_row = col.row(align=True)
        input_row.scale_y = 1.3
        input_row.prop(props, "prompt_input", text="", icon="CONSOLE", placeholder="Ask Blender AI... (e.g. 'Sahneyi incele')")

        # Submit button
        send_op = input_row.operator("ai_sidebar.send_prompt", text="", icon="PLAY")

        # If agent is currently active, show busy state and cancel
        if props.agent_status in ("PROCESSING", "EXECUTING_TOOL"):
            busy_box = col.box()
            busy_row = busy_box.row(align=True)
            busy_row.label(text=f"AI: {props.current_action}", icon="TIME")
            busy_row.operator("ai_sidebar.cancel_turn", text="Cancel", icon="CANCEL")
        else:
            hint_row = col.row(align=True)
            hint_row.scale_y = 0.8
            hint_row.label(text="Press Enter to send  •  Esc to close", icon="INFO")


CLASSES = (
    AISIDEBAR_PT_command_bar,
)


def register_command_bar():
    """Register command bar panel."""
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_command_bar():
    """Unregister command bar panel."""
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError):
            pass
