"""Modal operator managing events, input trapping, and draw lifecycle for the GPU HUD."""

import bpy
from bpy.types import Operator

from .state import overlay_state
from .renderer import draw_overlay_hud
from core.logging_utils import get_logger

_active_modal_operator = None
_logger = get_logger("gpu_overlay")


class AISIDEBAR_OT_viewport_hud(Operator):
    """Toggle the in-viewport floating AI HUD (Alt+Space)."""

    bl_idname = "ai_sidebar.viewport_hud"
    bl_label = "Blender AI HUD"
    bl_description = "Toggle in-viewport floating AI HUD"

    _draw_handler = None
    _timer = None

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        global _active_modal_operator

        # If already running, toggle off cleanly
        if overlay_state.is_open:
            self.cleanup(context)
            return {"FINISHED"}

        overlay_state.is_open = True
        _active_modal_operator = self

        # 1. Register draw handler on 3D Viewport
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_overlay_hud, (context,), "WINDOW", "POST_PIXEL"
        )

        # 2. Start animation/sync timer
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.03, window=context.window)

        # 3. Add modal handler
        wm.modal_handler_add(self)

        # Force redraw
        self.tag_redraw_view3d(context)
        return {"RUNNING_MODAL"}

    def cleanup(self, context):
        """Remove draw handler, timer, and reset overlay state."""
        global _active_modal_operator
        overlay_state.is_open = False
        _active_modal_operator = None

        if self._draw_handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler, "WINDOW")
            except Exception:
                pass
            self._draw_handler = None

        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None

        self.tag_redraw_view3d(context)

    @staticmethod
    def tag_redraw_view3d(context):
        """Safely tag 3D Viewport for redraw."""
        try:
            if context and context.area and context.area.type == "VIEW_3D":
                context.area.tag_redraw()
            elif context and context.screen:
                for a in context.screen.areas:
                    if a.type == "VIEW_3D":
                        a.tag_redraw()
        except Exception:
            _logger.exception("HUD redraw failed")

    def modal(self, context, event):
        if not overlay_state.is_open:
            self.cleanup(context)
            return {"FINISHED"}

        # ---------------------------------------------------------------------
        # 1. Periodic Timer (Blink & Agent Sync)
        # ---------------------------------------------------------------------
        if event.type == "TIMER":
            state_changed = overlay_state.update_blink()

            # Sync with AgentRuntime
            try:
                from ... import get_runtime
                runtime = get_runtime()
                if runtime:
                    is_proc = runtime.current_state.value in ("PROCESSING", "EXECUTING_TOOL")
                    if is_proc != overlay_state.is_processing:
                        overlay_state.is_processing = is_proc
                        state_changed = True

                    overlay_state.status_text = runtime.current_state.value
                    if runtime.last_result and runtime.last_result.final_text:
                        if overlay_state.last_response_text != runtime.last_result.final_text:
                            overlay_state.last_response_text = runtime.last_result.final_text
                            state_changed = True

                    live_text = getattr(runtime, "streaming_text", "")
                    if overlay_state.streaming_response_text != live_text:
                        overlay_state.streaming_response_text = live_text
                        state_changed = True

                    task_plan = getattr(runtime, "last_plan_summary", None)
                    if overlay_state.task_plan != task_plan:
                        overlay_state.task_plan = task_plan
                        state_changed = True

                    # Sync pending approval (plan review takes precedence as batch card)
                    plan_review = getattr(runtime, "pending_plan_review", None)
                    if plan_review is not None:
                        try:
                            appr_dict = plan_review.hud_summary()
                        except Exception:
                            _logger.exception("Plan review HUD serialization failed")
                            appr_dict = {
                                "kind": "plan",
                                "approval_id": plan_review.approval_id,
                                "title": plan_review.title,
                                "description": plan_review.description,
                                "steps_total": plan_review.steps_total,
                                "overall_risk": plan_review.overall_risk.value,
                            }
                        if overlay_state.pending_approval != appr_dict:
                            overlay_state.pending_approval = appr_dict
                            state_changed = True
                    else:
                        pending = getattr(runtime, "pending_approval", None)
                        if pending:
                            appr_dict = {
                                "approval_id": pending.approval_id,
                                "description": pending.human_readable_description,
                                "risk_level": pending.risk_level.value if hasattr(pending.risk_level, "value") else str(pending.risk_level),
                                "tool_name": pending.tool_name,
                            }
                            if overlay_state.pending_approval != appr_dict:
                                overlay_state.pending_approval = appr_dict
                                state_changed = True
                        elif overlay_state.pending_approval is not None:
                            overlay_state.pending_approval = None
                            state_changed = True
            except Exception:
                _logger.exception("HUD timer synchronization failed")
                overlay_state.status_text = "ERROR"
                overlay_state.is_processing = False
                state_changed = True

            if state_changed:
                self.tag_redraw_view3d(context)
            return {"PASS_THROUGH"}

        # ---------------------------------------------------------------------
        # 2. Mouse Move & Hover Detection
        # ---------------------------------------------------------------------
        if event.type == "MOUSEMOVE":
            hit = overlay_state.hit_test(event.mouse_region_x, event.mouse_region_y)
            if hit != overlay_state.hover_element:
                overlay_state.hover_element = hit
                self.tag_redraw_view3d(context)

            # If mouse is inside bar, swallow event to protect 3D scene underneath
            if hit is not None:
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        # ---------------------------------------------------------------------
        # 3. Mouse Clicks
        # ---------------------------------------------------------------------
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            hit = overlay_state.hit_test(event.mouse_region_x, event.mouse_region_y)
            if hit == "approve":
                if overlay_state.pending_approval:
                    appr_id = overlay_state.pending_approval["approval_id"]
                    self.approve_pending_action(appr_id)
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}
            elif hit == "reject":
                if overlay_state.pending_approval:
                    appr_id = overlay_state.pending_approval["approval_id"]
                    self.reject_pending_action(appr_id)
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}
            elif hit == "send":
                self.submit_current_prompt()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}
            elif hit == "cancel":
                self.cancel_current_turn()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}
            elif hit == "copy_response":
                response = overlay_state.streaming_response_text or overlay_state.last_response_text
                if response:
                    context.window_manager.clipboard = response
                    overlay_state.status_text = "COPIED"
                    self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}
            elif hit in ("input", "bar", "approval_card"):
                # Focus prompt
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            # Clicked outside bar: pass through to viewport
            return {"PASS_THROUGH"}

        # ---------------------------------------------------------------------
        # 4. Keyboard Controls
        # ---------------------------------------------------------------------
        if event.value == "PRESS":
            # If approval card is awaiting decision, intercept quick approval/rejection keys
            if overlay_state.pending_approval:
                appr_id = overlay_state.pending_approval["approval_id"]
                if event.type in ("Y", "A"):
                    self.approve_pending_action(appr_id)
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}
                elif event.type in ("N", "R"):
                    self.reject_pending_action(appr_id)
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}
                elif event.type == "ESC":
                    self.reject_pending_action(appr_id)
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}
                elif event.type == "RET" and not overlay_state.prompt_text:
                    self.approve_pending_action(appr_id)
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}

            # Esc: Close or clear
            if event.type == "ESC":
                if overlay_state.prompt_text:
                    overlay_state.reset_input()
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}
                else:
                    self.cleanup(context)
                    return {"FINISHED"}

            # Enter: Submit or Newline
            if event.type == "RET":
                if event.shift:
                    overlay_state.insert_text("\n")
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}
                else:
                    self.submit_current_prompt()
                    self.tag_redraw_view3d(context)
                    return {"RUNNING_MODAL"}

            # Backspace & Delete
            if event.type in ("BACKSPACE", "BACK_SPACE"):
                if event.ctrl:
                    overlay_state.delete_word_backward()
                else:
                    overlay_state.delete_backward()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            if event.type in ("DEL", "DELETE"):
                overlay_state.delete_forward()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            # Cursor Navigation
            if event.type == "LEFT_ARROW":
                overlay_state.move_cursor_left()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            if event.type == "RIGHT_ARROW":
                overlay_state.move_cursor_right()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            if event.type == "HOME":
                overlay_state.move_cursor_home()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            if event.type == "END":
                overlay_state.move_cursor_end()
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            # Clipboard Paste (Ctrl + V)
            if event.type == "V" and event.ctrl:
                clip = context.window_manager.clipboard
                if clip:
                    overlay_state.insert_text(clip)
                    self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            # Standard Text Character Input (includes Turkish & Unicode)
            if event.unicode:
                overlay_state.insert_text(event.unicode)
                self.tag_redraw_view3d(context)
                return {"RUNNING_MODAL"}

            # Trap all other keys while HUD is open so Blender viewport shortcuts aren't fired accidentally
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def submit_current_prompt(self):
        """Submit the prompt to AgentRuntime."""
        prompt = overlay_state.prompt_text.strip()
        if not prompt:
            return
        try:
            from ... import get_runtime
            runtime = get_runtime()
            if runtime:
                # Keep the submitted user turn visible after the input buffer
                # is cleared, so a failed/slow request is not mistaken for a
                # click that never reached the runtime.
                overlay_state.last_prompt_text = prompt
                overlay_state.reset_input()
                overlay_state.is_processing = True
                overlay_state.status_text = "PROCESSING"
                overlay_state.last_response_text = ""
                overlay_state.streaming_response_text = ""
                overlay_state.task_plan = None
                runtime.submit_prompt(prompt)
        except Exception as exc:
            _logger.exception("HUD prompt submission failed")
            overlay_state.is_processing = False
            overlay_state.status_text = "ERROR"
            overlay_state.last_response_text = f"Error: {exc}"

    def cancel_current_turn(self):
        """Cancel active agent operation."""
        try:
            from ... import get_runtime
            runtime = get_runtime()
            if runtime:
                runtime.cancel_current_turn()
                overlay_state.pending_approval = None
                overlay_state.is_processing = False
                overlay_state.status_text = "IDLE"
        except Exception as exc:
            _logger.exception("HUD turn cancellation failed")
            overlay_state.is_processing = False
            overlay_state.status_text = "ERROR"
            overlay_state.last_response_text = f"Cancel failed: {exc}"

    def approve_pending_action(self, approval_id: str):
        """Approve the pending action on the active agent runtime."""
        try:
            from ... import get_runtime
            runtime = get_runtime()
            if runtime:
                if approval_id.startswith("plan_") and hasattr(runtime, "approve_plan"):
                    try:
                        runtime.approve_plan(approval_id)
                    except Exception:
                        runtime.approve(approval_id)
                else:
                    runtime.approve(approval_id)
                overlay_state.pending_approval = None
        except Exception as exc:
            _logger.exception("HUD approval failed for %s", approval_id)
            overlay_state.status_text = "ERROR"
            overlay_state.last_response_text = f"Approval failed: {exc}"

    def reject_pending_action(self, approval_id: str):
        """Reject the pending action on the active agent runtime."""
        try:
            from ... import get_runtime
            runtime = get_runtime()
            if runtime:
                if approval_id.startswith("plan_") and hasattr(runtime, "reject_plan"):
                    try:
                        runtime.reject_plan(approval_id)
                    except Exception:
                        runtime.reject(approval_id)
                else:
                    runtime.reject(approval_id)
                overlay_state.pending_approval = None
        except Exception as exc:
            _logger.exception("HUD rejection failed for %s", approval_id)
            overlay_state.status_text = "ERROR"
            overlay_state.last_response_text = f"Rejection failed: {exc}"


CLASSES = (
    AISIDEBAR_OT_viewport_hud,
)


def register_modal():
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_modal():
    global _active_modal_operator
    if _active_modal_operator:
        try:
            _active_modal_operator.cleanup(bpy.context)
        except Exception:
            pass
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError):
            pass
