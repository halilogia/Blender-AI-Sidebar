"""3D Viewport N-Panel definition for Blender AI Sidebar."""

import textwrap
import bpy
from bpy.types import Panel


class AISIDEBAR_PT_main_panel(Panel):
    """Main panel located in the 3D Viewport Sidebar (N-Panel)."""

    bl_label = "AI Copilot"
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

        from .. import get_runtime

        runtime = get_runtime()

        # ---------------------------------------------------------------------
        # 1. Status Header Box
        # ---------------------------------------------------------------------
        status_box = layout.box()
        status_row = status_box.row(align=True)

        status = props.agent_status
        if status == "IDLE":
            icon = "CHECKMARK"
            status_text = "Status: IDLE"
        elif status == "PROCESSING":
            icon = "TIME"
            status_text = "Status: PROCESSING (Thinking)"
        elif status == "EXECUTING_TOOL":
            icon = "TOOL_SETTINGS"
            status_text = "Status: EXECUTING_TOOL"
        elif status == "ERROR":
            icon = "ERROR"
            status_text = "Status: ERROR"
        else:
            icon = "INFO"
            status_text = f"Status: {status}"

        status_row.label(text=status_text, icon=icon)

        # Action / Summary Sub-row
        action_row = status_box.row()
        action_row.label(text=f"Action: {props.current_action}", icon="FORWARD")

        # ---------------------------------------------------------------------
        # 2. Session History (UIList)
        # ---------------------------------------------------------------------
        hist_header = layout.row(align=True)
        hist_header.label(text="Session History:", icon="PREVIEW")
        hist_header.operator("ai_sidebar.clear_history", text="", icon="TRASH")

        layout.template_list(
            "AISIDEBAR_UL_history",
            "",
            props,
            "history",
            props,
            "history_index",
            rows=5,
        )

        # ---------------------------------------------------------------------
        # 3. Turn Detail Box (Bounded Textwrap)
        # ---------------------------------------------------------------------
        detail_box = layout.box()
        detail_box.label(text="Event Details:", icon="INFO")

        has_selection = bool(props.history) and 0 <= props.history_index < len(props.history)
        if has_selection:
            selected_item = props.history[props.history_index]

            # Try to fetch rich python detail from RuntimeHistory
            detail_text = selected_item.summary
            if runtime and hasattr(runtime, "history"):
                py_item = runtime.history.get_by_id(selected_item.item_id)
                if py_item and py_item.detail:
                    detail_text = py_item.detail

            # Text wrapping bounded to 12 lines
            raw_lines = detail_text.splitlines()
            wrapped_lines = []
            for rline in raw_lines:
                if rline.strip():
                    wrapped_lines.extend(textwrap.wrap(rline, width=38) or [rline])
                else:
                    wrapped_lines.append("")

            display_lines = wrapped_lines[:12]
            for line in display_lines:
                detail_box.label(text=line)

            if len(wrapped_lines) > 12:
                detail_box.label(
                    text=f"... (+{len(wrapped_lines) - 12} lines truncated)",
                    icon="THREE_DOTS",
                )
        else:
            detail_box.label(text="Select an item above to view details.", icon="DOT")

        # ---------------------------------------------------------------------
        # 4. Prompt Input & Actions
        # ---------------------------------------------------------------------
        input_box = layout.box()
        input_box.label(text="Prompt / Command:", icon="CONSOLE")
        input_box.prop(props, "prompt_input", text="")

        btn_row = input_box.row(align=True)
        btn_row.scale_y = 1.25

        # Send Operator
        send_op = btn_row.operator("ai_sidebar.send_prompt", text="Send", icon="PLAY")

        # Cancel Operator (enabled only when busy)
        cancel_op = btn_row.operator("ai_sidebar.cancel_turn", text="Cancel", icon="CANCEL")


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
        except (RuntimeError, ValueError):
            pass
