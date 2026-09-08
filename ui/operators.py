"""Blender operators for AI Sidebar user interactions."""

import bpy
from bpy.types import Operator


class AISIDEBAR_OT_open_command_bar(Operator):
    """Open the floating AI Command Bar in the 3D Viewport."""

    bl_idname = "ai_sidebar.open_command_bar"
    bl_label = "Open Command Bar"
    bl_description = "Open the floating AI Command Bar (Alt+Space)"

    def execute(self, context):
        props = getattr(context.window_manager, "ai_sidebar", None)
        if props:
            props.ui_mode = "COMMAND_BAR"
        if not bpy.app.background:
            try:
                bpy.ops.wm.call_panel(name="AISIDEBAR_PT_command_bar", keep_open=True)
            except Exception:
                pass
        return {"FINISHED"}


class AISIDEBAR_OT_open_conversation(Operator):
    """Open the floating AI Conversation Drawer in the 3D Viewport."""

    bl_idname = "ai_sidebar.open_conversation"
    bl_label = "Open Conversation Drawer"
    bl_description = "Open the floating AI Conversation Drawer"

    def execute(self, context):
        props = getattr(context.window_manager, "ai_sidebar", None)
        if props:
            props.ui_mode = "CONVERSATION"
        if not bpy.app.background:
            try:
                bpy.ops.wm.call_panel(name="AISIDEBAR_PT_conversation_drawer", keep_open=True)
            except Exception:
                pass
        return {"FINISHED"}


class AISIDEBAR_OT_send_prompt(Operator):
    """Submit prompt to the autonomous AI agent runtime."""

    bl_idname = "ai_sidebar.send_prompt"
    bl_label = "Send"
    bl_description = "Submit natural language prompt to AI agent"

    @classmethod
    def poll(cls, context):
        props = getattr(context.window_manager, "ai_sidebar", None)
        if not props or not props.prompt_input.strip():
            return False
        # Do not allow sending if already active
        return props.agent_status in ("IDLE", "ERROR")

    def execute(self, context):
        from .. import get_runtime

        runtime = get_runtime()
        if runtime is None:
            self.report({"ERROR"}, "AI Sidebar Agent Runtime is not active.")
            return {"CANCELLED"}

        props = context.window_manager.ai_sidebar
        prompt = props.prompt_input.strip()
        if not prompt:
            return {"CANCELLED"}

        # Clear input field immediately, switch UI mode to CONVERSATION, and dispatch turn
        props.prompt_input = ""
        props.live_streaming_text = ""
        props.agent_status = "PROCESSING"
        props.current_action = "Thinking..."
        props.ui_mode = "CONVERSATION"

        runtime.submit_prompt(prompt)

        # Transition to Conversation Drawer view
        if not bpy.app.background:
            try:
                bpy.ops.wm.call_panel(name="AISIDEBAR_PT_conversation_drawer", keep_open=True)
            except Exception:
                pass

        return {"FINISHED"}


class AISIDEBAR_OT_cancel_turn(Operator):
    """Cancel the active agent operation."""

    bl_idname = "ai_sidebar.cancel_turn"
    bl_label = "Cancel"
    bl_description = "Cancel currently executing agent turn"

    @classmethod
    def poll(cls, context):
        props = getattr(context.window_manager, "ai_sidebar", None)
        return props is not None and props.agent_status in ("PROCESSING", "EXECUTING_TOOL")

    def execute(self, context):
        from .. import get_runtime

        runtime = get_runtime()
        if runtime is not None:
            runtime.cancel_current_turn()
        props = getattr(context.window_manager, "ai_sidebar", None)
        if props:
            props.agent_status = "IDLE"
            props.current_action = "Cancelled"
            props.live_streaming_text = ""
        return {"FINISHED"}


class AISIDEBAR_OT_clear_history(Operator):
    """Clear conversation turns and tool history from the session."""

    bl_idname = "ai_sidebar.clear_history"
    bl_label = "Clear History"
    bl_description = "Clear current session history"

    def execute(self, context):
        from .. import get_runtime

        props = getattr(context.window_manager, "ai_sidebar", None)
        if props:
            props.history.clear()
            props.history_index = -1
            props.live_streaming_text = ""

        runtime = get_runtime()
        if runtime is not None:
            runtime.clear_history()

        return {"FINISHED"}


CLASSES = (
    AISIDEBAR_OT_open_command_bar,
    AISIDEBAR_OT_open_conversation,
    AISIDEBAR_OT_send_prompt,
    AISIDEBAR_OT_cancel_turn,
    AISIDEBAR_OT_clear_history,
)


def register_operators():
    """Register operator classes."""
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_operators():
    """Unregister operator classes."""
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
