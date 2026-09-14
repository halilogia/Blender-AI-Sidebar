"""3D Viewport N-Panel minimal launcher for Blender AI Sidebar."""

import bpy
from bpy.types import Panel


class AISIDEBAR_PT_main_panel(Panel):
    """Launcher, live status, and ordered conversation timeline."""

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
        # 1. Primary Action: Launch In-Viewport AI HUD (Higgsfield Style)
        # ---------------------------------------------------------------------
        col = layout.column(align=True)
        col.scale_y = 1.4
        col.operator("ai_sidebar.viewport_hud", text="✦ Open AI HUD", icon="WINDOW")

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
            box.label(text="AI Agent: Ready", icon="CHECKMARK")
        elif status in ("PROCESSING", "EXECUTING_TOOL"):
            box.label(text=f"AI: {props.current_action}", icon="TIME")
            box.operator("ai_sidebar.cancel_turn", text="Cancel Turn", icon="CANCEL")
        elif status == "ERROR":
            box.label(text="AI: Error encountered", icon="ERROR")
        else:
            box.label(text=f"Status: {status}", icon="INFO")

        # ---------------------------------------------------------------------
        # 3. Ordered conversation / prompt queue
        # ---------------------------------------------------------------------
        history = list(getattr(props, "history", []))
        conversation_box = layout.box()
        header = conversation_box.row(align=True)
        header.label(text=f"Conversation ({len(history)})", icon="TEXT")
        queued_count = int(getattr(props, "queued_count", 0))
        if queued_count:
            header.label(text=f"{queued_count} queued", icon="TIME")

        if not history:
            conversation_box.label(text="No messages yet.", icon="INFO")
        else:
            # RuntimeHistory is chronological. Showing the newest entries in
            # this order makes queued prompts visible without hiding their
            # position in the conversation.
            for item in history[-12:]:
                row = conversation_box.row(align=True)
                row.label(text=_status_marker(item.status), icon="DOT")
                row.label(text=_clip(item.title, 58))
                summary = _clip(item.summary, 88)
                if summary and summary != item.title:
                    detail_row = conversation_box.row()
                    detail_row.label(text=f"  {summary}")

        if status == "PENDING_APPROVAL":
            approval_row = conversation_box.row(align=True)
            approval_row.operator("ai_sidebar.approve_action", text="Approve", icon="CHECKMARK")
            approval_row.operator("ai_sidebar.reject_action", text="Reject", icon="X")
        if history:
            conversation_box.operator("ai_sidebar.clear_history", text="Clear Conversation", icon="TRASH")


def _clip(value, limit):
    """Keep N-panel rows readable while retaining full text in the HUD."""
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _status_marker(status):
    return {
        "QUEUED": "○",
        "RUNNING": "●",
        "PROCESSING": "●",
        "PENDING": "◌",
        "OK": "✓",
        "SENT": "✓",
        "COMPLETED": "✓",
        "ERROR": "✕",
        "FAILED": "✕",
        "CANCELLED": "—",
    }.get(str(status or "").upper(), "·")


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
