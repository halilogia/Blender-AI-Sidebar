"""Blender UIList implementation for AI Sidebar history display."""

import bpy
from bpy.types import UIList


class AISIDEBAR_UL_history(UIList):
    """Compact history view displaying conversation turns and tool events."""

    bl_idname = "AISIDEBAR_UL_history"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_property,
        index=0,
        flt_flag=0,
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)

            # 1. Icon reflecting item category
            kind = getattr(item, "kind", "SYSTEM")
            if kind == "USER":
                icon_name = "USER"
            elif kind == "ASSISTANT":
                icon_name = "SCRIPT"
            elif kind == "TOOL":
                icon_name = "TOOL_SETTINGS"
            elif kind == "ERROR":
                icon_name = "ERROR"
            else:
                icon_name = "INFO"

            row.label(text="", icon=icon_name)

            # 2. Main Title / Summary
            row.label(text=item.title or "Event")

            # 3. Compact Status Badge (Right aligned)
            sub = row.row()
            sub.alignment = "RIGHT"
            status = getattr(item, "status", "")
            if status in ("OK", "DONE"):
                sub.label(text=status, icon="CHECKMARK")
            elif status in ("FAIL", "ERROR"):
                sub.label(text=status, icon="CANCEL")
            elif status == "CANCEL":
                sub.label(text=status, icon="X")
            elif status == "SENT":
                sub.label(text=status, icon="FORWARD")
            else:
                sub.label(text=status)
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="DOT")


CLASSES = (
    AISIDEBAR_UL_history,
)


def register_uilist():
    """Register UIList class."""
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_uilist():
    """Unregister UIList class."""
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
