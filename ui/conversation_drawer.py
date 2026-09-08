"""AI Conversation & Tool Execution Drawer for Blender 3D Viewport."""

import textwrap
import bpy
from bpy.types import Panel


class AISIDEBAR_PT_conversation_drawer(Panel):
    """Rich conversational drawer displaying streaming responses, tool executions, and turn history."""

    bl_label = "Blender AI Conversation"
    bl_idname = "AISIDEBAR_PT_conversation_drawer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_ui_units_x = 28

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
        # 1. Header & Live Status
        # ---------------------------------------------------------------------
        header_row = layout.row(align=True)
        status = props.agent_status

        if status == "IDLE":
            header_row.label(text="AI: Ready", icon="CHECKMARK")
        elif status in ("PROCESSING", "EXECUTING_TOOL"):
            header_row.label(text=f"AI: {props.current_action}", icon="TIME")
            header_row.operator("ai_sidebar.cancel_turn", text="Cancel", icon="CANCEL")
        elif status == "ERROR":
            header_row.label(text="AI: Error", icon="ERROR")
        else:
            header_row.label(text=f"AI: {status}", icon="INFO")

        header_row.operator("ai_sidebar.clear_history", text="", icon="TRASH")

        layout.separator()

        # ---------------------------------------------------------------------
        # 2. Conversation Items for Current / Most Recent Turn
        # ---------------------------------------------------------------------
        items = []
        if runtime and hasattr(runtime, "history") and runtime.history.items:
            # Find the most recent turn_id
            all_items = runtime.history.items
            target_turn_id = all_items[-1].turn_id if all_items else None
            if target_turn_id:
                items = [it for it in all_items if it.turn_id == target_turn_id]
            else:
                items = all_items[-10:]

        if not items and not props.history:
            empty_box = layout.box()
            empty_box.label(text="No conversation yet.", icon="INFO")
            empty_box.label(text="Press Alt+Space to enter a command.", icon="FORWARD")
        else:
            # Render items of the current turn
            for it in items:
                kind = getattr(it, "kind", "SYSTEM")

                if kind == "USER":
                    user_box = layout.box()
                    user_header = user_box.row()
                    user_header.label(text="You", icon="USER")
                    self._draw_wrapped_text(user_box, it.detail or it.summary)

                elif kind == "TOOL":
                    tool_box = layout.box()
                    tool_row = tool_box.row(align=True)
                    tool_row.label(text=it.title or "Tool Call", icon="TOOL_SETTINGS")

                    # Status badge
                    st = getattr(it, "status", "OK")
                    if st in ("OK", "DONE"):
                        tool_row.label(text="Completed", icon="CHECKMARK")
                    elif st in ("RUNNING", "IN_PROGRESS"):
                        tool_row.label(text="Executing...", icon="TIME")
                    elif st in ("FAIL", "ERROR"):
                        tool_row.label(text="Failed", icon="CANCEL")
                    else:
                        tool_row.label(text=st)

                    if it.summary and it.summary != it.title:
                        sub_row = tool_box.row()
                        sub_row.scale_y = 0.85
                        sub_row.label(text=it.summary, icon="FORWARD")

                elif kind == "ASSISTANT":
                    ai_box = layout.box()
                    ai_header = ai_box.row()
                    ai_header.label(text="Blender AI", icon="SCRIPT")

                    content = it.detail or it.summary or ""
                    # If actively streaming and live text is present
                    if status in ("PROCESSING", "EXECUTING_TOOL") and props.live_streaming_text:
                        content = props.live_streaming_text

                    self._draw_wrapped_text(ai_box, content)

                elif kind == "ERROR":
                    err_box = layout.box()
                    err_box.alert = True
                    err_box.label(text="Error Encountered", icon="ERROR")
                    self._draw_wrapped_text(err_box, it.detail or it.summary)

            # If currently thinking and no assistant bubble rendered yet
            if status in ("PROCESSING", "EXECUTING_TOOL") and not any(it.kind == "ASSISTANT" for it in items):
                live_box = layout.box()
                live_header = live_box.row()
                live_header.label(text="Blender AI (Thinking...)", icon="TIME")
                if props.live_streaming_text:
                    self._draw_wrapped_text(live_box, props.live_streaming_text)
                else:
                    live_box.label(text=props.current_action, icon="FORWARD")

        # ---------------------------------------------------------------------
        # 3. Footer Actions (New prompt / Quick launch)
        # ---------------------------------------------------------------------
        layout.separator()
        footer = layout.row(align=True)
        footer.scale_y = 1.2
        footer.operator("ai_sidebar.open_command_bar", text="New Prompt (Alt+Space)", icon="CONSOLE")


    def _draw_wrapped_text(self, layout, text: str, max_lines: int = 20, width: int = 42):
        """Helper to cleanly wrap multiline text across layout labels."""
        if not text:
            return
        lines = text.splitlines()
        wrapped = []
        for l in lines:
            if l.strip():
                wrapped.extend(textwrap.wrap(l, width=width) or [l])
            else:
                wrapped.append("")

        for wl in wrapped[:max_lines]:
            layout.label(text=wl)

        if len(wrapped) > max_lines:
            layout.label(text=f"... (+{len(wrapped) - max_lines} lines truncated)", icon="THREE_DOTS")


CLASSES = (
    AISIDEBAR_PT_conversation_drawer,
)


def register_conversation_drawer():
    """Register conversation drawer panel."""
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_conversation_drawer():
    """Unregister conversation drawer panel."""
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError):
            pass
