"""Session persistence for saving and restoring RollingMemory inside .blend files.

Provides Blender-native session memory persistence via Scene custom properties.
Binds to Blender's save_pre and load_post application handlers.
Main-thread only. Zero secrets, zero API keys, zero image bytes stored.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from agent.memory import (
    SESSION_MEMORY_PROPERTY_NAME,
    RollingMemory,
    deserialize_session_memory,
    serialize_session_memory,
)

logger = logging.getLogger(__name__)

try:
    import bpy
    from bpy.app.handlers import persistent
except ImportError:
    bpy = None
    persistent = lambda f: f  # No-op decorator when running outside Blender

_runtime_getter: Optional[Callable[[], Optional[Any]]] = None


def set_runtime_getter(getter: Optional[Callable[[], Optional[Any]]]) -> None:
    """Configure the runtime resolver callback."""
    global _runtime_getter
    _runtime_getter = getter


def assert_main_thread() -> None:
    """Guard ensuring bpy interaction occurs strictly on the main thread."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            f"Session persistence accessed from background thread '{threading.current_thread().name}'. "
            "All bpy and scene persistence interactions must occur on the main thread."
        )


def get_active_scene() -> Optional[Any]:
    """Safely obtain the active Blender scene on the main thread."""
    if bpy is None:
        return None
    assert_main_thread()
    scene = getattr(bpy.context, "scene", None)
    if scene is None and hasattr(bpy.data, "scenes") and bpy.data.scenes:
        scene = bpy.data.scenes[0]
    return scene


def resolve_runtime(runtime: Optional[Any] = None) -> Optional[Any]:
    """Resolve the active AgentRuntime instance."""
    if runtime is not None:
        return runtime
    if _runtime_getter is not None:
        try:
            return _runtime_getter()
        except Exception:
            return None
    return None


def save_session_memory_to_scene(
    scene: Optional[Any] = None,
    runtime: Optional[Any] = None,
) -> bool:
    """Serialize active runtime session memory and write to target scene custom property.

    Args:
        scene: Target Blender Scene or None (defaults to active scene).
        runtime: Active AgentRuntime or None (defaults to resolved runtime).

    Returns:
        True if memory was serialized and saved, False otherwise.
    """
    if scene is None:
        scene = get_active_scene()
    if scene is None:
        return False

    rt = resolve_runtime(runtime)
    if rt is None:
        return False

    try:
        memory = rt.export_session_memory()
        if memory is None:
            if SESSION_MEMORY_PROPERTY_NAME in scene:
                try:
                    del scene[SESSION_MEMORY_PROPERTY_NAME]
                except Exception:
                    scene[SESSION_MEMORY_PROPERTY_NAME] = ""
            return False

        serialized_payload = serialize_session_memory(memory)
        scene[SESSION_MEMORY_PROPERTY_NAME] = serialized_payload
        return True
    except Exception as exc:
        logger.warning("Failed to save session memory to scene: %s", exc)
        return False


def load_session_memory_from_scene(
    scene: Optional[Any] = None,
    runtime: Optional[Any] = None,
) -> bool:
    """Read session memory from target scene custom property and restore into runtime.

    Args:
        scene: Target Blender Scene or None (defaults to active scene).
        runtime: Active AgentRuntime or None (defaults to resolved runtime).

    Returns:
        True if memory was restored into runtime, False if empty or absent.
    """
    if scene is None:
        scene = get_active_scene()
    if scene is None:
        return False

    rt = resolve_runtime(runtime)
    if rt is None:
        return False

    try:
        raw_payload = scene.get(SESSION_MEMORY_PROPERTY_NAME)
        if not raw_payload:
            return False

        memory = deserialize_session_memory(raw_payload)
        if memory is None:
            logger.warning("Corrupted or incompatible session memory found in scene; skipped.")
            return False

        return rt.restore_session_memory(memory)
    except Exception as exc:
        logger.warning("Failed to load session memory from scene: %s", exc)
        return False


@persistent
def on_blend_save_pre(*args: Any) -> None:
    """Blender save_pre application handler: serialize session memory to scene before saving."""
    try:
        save_session_memory_to_scene()
    except Exception as exc:
        logger.warning("Error in on_blend_save_pre handler: %s", exc)


@persistent
def on_blend_load_post(*args: Any) -> None:
    """Blender load_post application handler: restore session memory from loaded scene."""
    try:
        load_session_memory_from_scene()
    except Exception as exc:
        logger.warning("Error in on_blend_load_post handler: %s", exc)


def register_session_handlers() -> None:
    """Register persistent save_pre and load_post handlers in Blender."""
    if bpy is None or not hasattr(bpy, "app") or not hasattr(bpy.app, "handlers"):
        return

    if on_blend_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(on_blend_save_pre)

    if on_blend_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_blend_load_post)


def unregister_session_handlers() -> None:
    """Unregister persistent save_pre and load_post handlers from Blender."""
    if bpy is None or not hasattr(bpy, "app") or not hasattr(bpy.app, "handlers"):
        return

    if on_blend_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(on_blend_save_pre)

    if on_blend_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_blend_load_post)
