"""3D Viewport N-Panel definition for Blender AI Sidebar (Inspector & Control role)."""

import textwrap
import bpy
from bpy.types import Panel


class AISIDEBAR_PT_main_panel(Panel):
    """Inspector & Control panel located in the 3D Viewport Sidebar (N-Panel)."""

    bl_label = "AI Inspector & Controls"
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

        runtime = None
        try:
            from .. import get_runtime
            runtime = get_runtime()
        except (ImportError, ValueError):
            import sys
            mod = sys.modules.get("blender_ai_sidebar")
            if mod and hasattr(mod, "get_runtime"):
                runtime = mod.get_runtime()

        # ---------------------------------------------------------------------
        # 1. Quick Launch Actions
        # ---------------------------------------------------------------------
        launch_col = layout.column(align=True)
        launch_col.scale_y = 1.3
        launch_col.operator("ai_sidebar.open_web_ui", text="✦ Open Web UI (Modern)", icon="WINDOW")

        nav_row = launch_col.row(align=True)
        nav_row.scale_y = 0.9
        nav_row.operator("ai_sidebar.open_command_bar", text="Command Bar", icon="CONSOLE")
        nav_row.operator("ai_sidebar.open_conversation", text="Drawer", icon="SCRIPT")
        if props.agent_status in ("PROCESSING", "EXECUTING_TOOL"):
            nav_row.operator("ai_sidebar.cancel_turn", text="Cancel", icon="CANCEL")

        layout.separator()

        # ---------------------------------------------------------------------
        # 2. Context & Scene State
        # ---------------------------------------------------------------------
        context_box = layout.box()
        context_box.label(text="Scene Context:", icon="WORLD")
        crow = context_box.row(align=True)
        crow.label(text=f"Scene: {context.scene.name}")
        crow.label(text=f"Objects: {len(context.scene.objects)}")

        active_name = context.active_object.name if context.active_object else "None"
        context_box.label(text=f"Active: {active_name}", icon="OBJECT_DATA")

        # ---------------------------------------------------------------------
        # 3. Agent & Model Status
        # ---------------------------------------------------------------------
        status_box = layout.box()
        status_box.label(text="Agent Status:", icon="SETTINGS")
        srow = status_box.row(align=True)

        status = props.agent_status
        if status == "IDLE":
            srow.label(text="Status: IDLE", icon="CHECKMARK")
        elif status == "PROCESSING":
            srow.label(text="Status: PROCESSING", icon="TIME")
        elif status == "EXECUTING_TOOL":
            srow.label(text="Status: TOOL EXEC", icon="TOOL_SETTINGS")
        elif status == "ERROR":
            srow.label(text="Status: ERROR", icon="ERROR")
        else:
            srow.label(text=f"Status: {status}", icon="INFO")

        status_box.label(text=f"Action: {props.current_action}", icon="FORWARD")

        # ---------------------------------------------------------------------
        # 4. Session History & Inspector (UIList)
        # ---------------------------------------------------------------------
        hist_header = layout.row(align=True)
        hist_header.label(text="Session History:", icon="FILE_TEXT")
        hist_header.operator("ai_sidebar.clear_history", text="", icon="TRASH")

        layout.template_list(
            "AISIDEBAR_UL_history",
            "",
            props,
            "history",
            props,
            "history_index",
            rows=4,
        )

        # ---------------------------------------------------------------------
        # 5. Event Detail Box
        # ---------------------------------------------------------------------
        detail_box = layout.box()
        detail_box.label(text="Event Inspector:", icon="INFO")

        has_selection = bool(props.history) and 0 <= props.history_index < len(props.history)
        if has_selection:
            selected_item = props.history[props.history_index]

            detail_text = selected_item.summary
            if runtime and hasattr(runtime, "history"):
                py_item = runtime.history.get_by_id(selected_item.item_id)
                if py_item and py_item.detail:
                    detail_text = py_item.detail

            raw_lines = detail_text.splitlines()
            wrapped_lines = []
            for rline in raw_lines:
                if rline.strip():
                    wrapped_lines.extend(textwrap.wrap(rline, width=38) or [rline])
                else:
                    wrapped_lines.append("")

            for line in wrapped_lines[:10]:
                detail_box.label(text=line)

            if len(wrapped_lines) > 10:
                detail_box.label(
                    text=f"... (+{len(wrapped_lines) - 10} lines truncated)",
                    icon="THREE_DOTS",
                )
        else:
            detail_box.label(text="Select an item above to inspect.", icon="DOT")


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
