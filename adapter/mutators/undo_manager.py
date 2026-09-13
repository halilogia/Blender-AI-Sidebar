"""UndoManager for atomic undo/redo history steps in Blender 5.2.1.

Ensures that every mutation executed by the agent creates an atomic, discrete
undo point matching native Blender Ctrl+Z behavior.
"""

from typing import Optional
import bpy


def push_undo_step(message: str) -> bool:
    """Register an atomic undo step in Blender's global undo stack.

    Args:
        message: Human-readable description of the operation stored in undo history.

    Returns:
        bool: True if undo step was recorded, False otherwise.
    """
    # 1. Ensure global undo preference is active
    try:
        if hasattr(bpy.context, "preferences") and hasattr(bpy.context.preferences, "edit"):
            bpy.context.preferences.edit.use_global_undo = True
    except Exception:
        pass

    # 2. Try direct undo_push if poll passes (normal GUI operator context)
    try:
        if bpy.ops.ed.undo_push.poll():
            bpy.ops.ed.undo_push(message=message)
            return True
    except Exception:
        pass

    # 3. If direct poll fails (e.g. headless background execution or timer callback),
    # invoke with window/area context temp_override.
    try:
        wm = getattr(bpy.context, "window_manager", None)
        if wm and wm.windows:
            win = wm.windows[0]
            screen = win.screen
            workspace = win.workspace
            area = next(
                (a for a in screen.areas if a.type == "VIEW_3D"),
                screen.areas[0] if screen.areas else None,
            )
            region = (
                next(
                    (r for r in area.regions if r.type == "WINDOW"),
                    area.regions[0] if area.regions else None,
                )
                if area
                else None
            )

            with bpy.context.temp_override(
                window=win,
                screen=screen,
                workspace=workspace,
                area=area,
                region=region,
            ):
                if bpy.ops.ed.undo_push.poll():
                    bpy.ops.ed.undo_push(message=message)
                    return True
                else:
                    # Force call if possible
                    bpy.ops.ed.undo_push(message=message)
                    return True
    except Exception:
        pass

    return False


def perform_undo() -> bool:
    """Perform a single undo step (Ctrl+Z equivalent). Useful for testing & rollbacks.

    Returns:
        bool: True if undo succeeded, False otherwise.
    """
    try:
        if bpy.ops.ed.undo.poll():
            bpy.ops.ed.undo()
            return True
    except Exception:
        pass

    try:
        wm = getattr(bpy.context, "window_manager", None)
        if wm and wm.windows:
            win = wm.windows[0]
            screen = win.screen
            workspace = win.workspace
            area = next(
                (a for a in screen.areas if a.type == "VIEW_3D"),
                screen.areas[0] if screen.areas else None,
            )
            region = (
                next(
                    (r for r in area.regions if r.type == "WINDOW"),
                    area.regions[0] if area.regions else None,
                )
                if area
                else None
            )

            with bpy.context.temp_override(
                window=win,
                screen=screen,
                workspace=workspace,
                area=area,
                region=region,
            ):
                res = bpy.ops.ed.undo()
                return "FINISHED" in res
    except Exception:
        pass

    return False


def perform_redo() -> bool:
    """Perform a single redo step (Ctrl+Shift+Z equivalent). Useful for testing.

    Returns:
        bool: True if redo succeeded, False otherwise.
    """
    try:
        if bpy.ops.ed.redo.poll():
            bpy.ops.ed.redo()
            return True
    except Exception:
        pass

    try:
        wm = getattr(bpy.context, "window_manager", None)
        if wm and wm.windows:
            win = wm.windows[0]
            screen = win.screen
            workspace = win.workspace
            area = next(
                (a for a in screen.areas if a.type == "VIEW_3D"),
                screen.areas[0] if screen.areas else None,
            )
            region = (
                next(
                    (r for r in area.regions if r.type == "WINDOW"),
                    area.regions[0] if area.regions else None,
                )
                if area
                else None
            )

            with bpy.context.temp_override(
                window=win,
                screen=screen,
                workspace=workspace,
                area=area,
                region=region,
            ):
                res = bpy.ops.ed.redo()
                return "FINISHED" in res
    except Exception:
        pass

    return False
