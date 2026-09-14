"""Blender operators for AI Sidebar user interactions."""

import bpy
from bpy.types import Operator
from core.logging_utils import get_logger


_logger = get_logger("operators")


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
        # Active submissions are accepted and become visible FIFO queue rows.
        return props.agent_status in ("IDLE", "ERROR", "PROCESSING", "EXECUTING_TOOL", "PENDING_APPROVAL")

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

        try:
            runtime.submit_prompt(prompt)
            return {"FINISHED"}
        except Exception as exc:
            _logger.exception("Sidebar prompt submission failed")
            props.agent_status = "ERROR"
            props.current_action = "Error encountered"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


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
            try:
                runtime.cancel_current_turn()
            except Exception as exc:
                _logger.exception("Sidebar turn cancellation failed")
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
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


class AISIDEBAR_OT_approve_action(Operator):
    """Approve the active pending tool action."""

    bl_idname = "ai_sidebar.approve_action"
    bl_label = "Approve Action"
    bl_description = "Approve execution of currently pending tool action"

    approval_id: bpy.props.StringProperty(name="Approval ID", default="")

    @classmethod
    def poll(cls, context):
        from .. import get_runtime
        runtime = get_runtime()
        if runtime is None:
            return False
        return runtime.pending_approval is not None or getattr(runtime, "pending_plan_review", None) is not None

    def execute(self, context):
        from .. import get_runtime
        runtime = get_runtime()
        if not runtime:
            self.report({"WARNING"}, "No action is currently pending approval.")
            return {"CANCELLED"}
        target_id = self.approval_id
        if not target_id:
            if getattr(runtime, "pending_plan_review", None) is not None:
                target_id = runtime.pending_plan_review.approval_id
            elif runtime.pending_approval is not None:
                target_id = runtime.pending_approval.approval_id
        if not target_id:
            self.report({"WARNING"}, "No action is currently pending approval.")
            return {"CANCELLED"}
        try:
            if target_id.startswith("plan_") and hasattr(runtime, "approve_plan"):
                try:
                    runtime.approve_plan(target_id)
                except Exception:
                    runtime.approve(target_id)
            else:
                runtime.approve(target_id)
            return {"FINISHED"}
        except Exception as exc:
            _logger.exception("Sidebar approval failed for %s", target_id)
            self.report({"ERROR"}, f"Approval failed: {str(exc)}")
            return {"CANCELLED"}


class AISIDEBAR_OT_reject_action(Operator):
    """Reject the active pending tool action."""

    bl_idname = "ai_sidebar.reject_action"
    bl_label = "Reject Action"
    bl_description = "Reject execution of currently pending tool action"

    approval_id: bpy.props.StringProperty(name="Approval ID", default="")

    @classmethod
    def poll(cls, context):
        from .. import get_runtime
        runtime = get_runtime()
        if runtime is None:
            return False
        return runtime.pending_approval is not None or getattr(runtime, "pending_plan_review", None) is not None

    def execute(self, context):
        from .. import get_runtime
        runtime = get_runtime()
        if not runtime:
            self.report({"WARNING"}, "No action is currently pending approval.")
            return {"CANCELLED"}

        target_id = self.approval_id
        if not target_id:
            if getattr(runtime, "pending_plan_review", None) is not None:
                target_id = runtime.pending_plan_review.approval_id
            elif runtime.pending_approval is not None:
                target_id = runtime.pending_approval.approval_id
        if not target_id:
            self.report({"WARNING"}, "No action is currently pending approval.")
            return {"CANCELLED"}
        try:
            if target_id.startswith("plan_") and hasattr(runtime, "reject_plan"):
                try:
                    runtime.reject_plan(target_id)
                except Exception:
                    runtime.reject(target_id)
            else:
                runtime.reject(target_id)
            return {"FINISHED"}
        except Exception as exc:
            _logger.exception("Sidebar rejection failed for %s", target_id)
            self.report({"ERROR"}, f"Rejection failed: {str(exc)}")
            return {"CANCELLED"}


CLASSES = (
    AISIDEBAR_OT_send_prompt,
    AISIDEBAR_OT_cancel_turn,
    AISIDEBAR_OT_clear_history,
    AISIDEBAR_OT_approve_action,
    AISIDEBAR_OT_reject_action,
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
