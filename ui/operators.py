"""Blender operators for AI Sidebar user interactions."""

import bpy
from bpy.types import Operator


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

        # Clear input field immediately and dispatch turn
        props.prompt_input = ""
        props.live_streaming_text = ""
        props.agent_status = "PROCESSING"
        props.current_action = "Thinking..."

        runtime.submit_prompt(prompt)
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
