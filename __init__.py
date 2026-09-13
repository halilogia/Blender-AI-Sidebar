"""Blender AI Sidebar — Autonomous AI Agent & Grounding Copilot for Blender."""

bl_info = {
    "name": "Blender AI Sidebar",
    "author": "Halil Emre",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > AI Sidebar / View3D > Alt+Space",
    "description": "Autonomous AI Agent & Grounding Copilot for Blender",
    "category": "Development",
}

import os
import sys
from typing import Optional

# Ensure the addon directory is in sys.path so that internal packages
# (core, tools, adapter, agent, ui) resolve reliably when loaded by Blender.
_addon_dir = os.path.dirname(os.path.abspath(__file__))
if _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

from .ui.preferences import register_preferences, unregister_preferences
from .ui.properties import register_properties, unregister_properties
from .ui.operators import register_operators, unregister_operators
from .ui.panel import register_panels, unregister_panels
from .ui.keymap import register_keymaps, unregister_keymaps
from .ui.header import register_header, unregister_header
from .ui.gpu_overlay import register as register_gpu_overlay, unregister as unregister_gpu_overlay
from .ui.timer_bridge import TimerBridge
from .adapter.blender_adapter import BlenderAdapter
from .tools.registry import ToolRegistry
from .tools.read_only.inspect_scene import InspectSceneTool
from .tools.read_only.inspect_selection import InspectSelectionTool
from .tools.read_only.inspect_object import InspectObjectTool
from .tools.read_only.inspect_material import InspectMaterialTool
from .tools.read_only.inspect_mesh import InspectMeshTool
from .tools.mutations.create_primitive import CreatePrimitiveTool
from .tools.mutations.transform_object import TransformObjectTool
from .tools.mutations.delete_object import DeleteObjectTool
from .tools.mutations.set_material import SetMaterialTool
from .tools.mutations.assign_material import AssignMaterialTool
from .tools.read_only.capture_viewport import CaptureViewportTool
from .tools.read_only.visual_verify import VisualVerifyTool
from .core.config import Config
from .agent.provider import BaseProvider
from .agent.mock_provider import MockProvider
from .agent.openai_provider import OpenAICompatibleProvider
from .agent.dispatcher import ToolDispatcher
from .agent.runtime import AgentRuntime
from .ui.preferences import get_effective_config
from .adapter.session_persistence import (
    register_session_handlers,
    unregister_session_handlers,
    set_runtime_getter,
    load_session_memory_from_scene,
)

_runtime: Optional[AgentRuntime] = None
_timer_bridge: Optional[TimerBridge] = None


def create_production_provider() -> BaseProvider:
    """Construct real OpenAICompatibleProvider from effective configuration."""
    import bpy
    cfg = get_effective_config()
    online_access = getattr(bpy.app, "online_access", True)
    return OpenAICompatibleProvider(config=cfg, online_access=online_access)


def update_runtime_config(config: Config) -> None:
    """Dynamically sync configuration changes to the running provider."""
    global _runtime
    if _runtime and hasattr(_runtime, "provider"):
        provider = _runtime.provider
        if hasattr(provider, "config"):
            provider.config = config
            effective_timeout = getattr(config, "timeout_seconds", 30.0)
            if hasattr(provider, "http_client"):
                provider.http_client.base_url = config.base_url
                provider.http_client.timeout = effective_timeout


def get_runtime() -> Optional[AgentRuntime]:
    """Retrieve the active extension agent runtime."""
    return _runtime


def get_timer_bridge() -> Optional[TimerBridge]:
    """Retrieve the active extension timer bridge."""
    return _timer_bridge


def register(provider: Optional[BaseProvider] = None):
    """Register all extension components, tools, runtime, and timer bridge."""
    global _runtime, _timer_bridge

    # Idempotency guard: if already registered, unregister cleanly first
    if _runtime is not None or _timer_bridge is not None:
        unregister()

    # 1. UI Preferences, Properties, Operators, Panels, Header, Keymaps & GPU Overlay
    register_preferences()
    register_properties()
    register_operators()
    register_panels()
    register_header()
    register_keymaps()
    register_gpu_overlay()

    # 2. Tool Registry (Inspection & Safe Mutation Tools)
    registry = ToolRegistry()
    registry.register(InspectSceneTool())
    registry.register(InspectSelectionTool())
    registry.register(InspectObjectTool())
    registry.register(InspectMaterialTool())
    registry.register(InspectMeshTool())
    registry.register(CreatePrimitiveTool())
    registry.register(TransformObjectTool())
    registry.register(DeleteObjectTool())
    registry.register(SetMaterialTool())
    registry.register(AssignMaterialTool())
    registry.register(CaptureViewportTool())
    registry.register(VisualVerifyTool())

    # 3. Adapter & Dispatcher
    adapter = BlenderAdapter()
    dispatcher = ToolDispatcher(registry=registry, adapter=adapter)

    # 4. Production Real Provider & Agent Runtime
    if provider is not None:
        effective_provider = provider
    elif os.environ.get("BLENDER_AI_USE_MOCK_PROVIDER") == "1":
        effective_provider = MockProvider()
    else:
        effective_provider = create_production_provider()

    _runtime = AgentRuntime(provider=effective_provider, dispatcher=dispatcher)

    # 5. Timer Bridge for Async Event Loop
    _timer_bridge = TimerBridge(runtime=_runtime, event_queue=_runtime.event_queue)
    _timer_bridge.register()

    # 6. Session Persistence (.blend save_pre and load_post handlers)
    set_runtime_getter(get_runtime)
    register_session_handlers()
    try:
        load_session_memory_from_scene(runtime=_runtime)
    except Exception:
        pass


def unregister():
    """Unregister all extension components and guarantee clean shutdown."""
    global _runtime, _timer_bridge

    # 0. Unregister session persistence handlers
    unregister_session_handlers()
    set_runtime_getter(None)

    # 1. Stop timer bridge
    if _timer_bridge is not None:
        _timer_bridge.unregister()
        _timer_bridge = None

    # 2. Shutdown runtime and background workers
    if _runtime is not None:
        _runtime.shutdown()
        _runtime = None

    # 3. Unregister UI & Keymaps
    unregister_gpu_overlay()
    unregister_keymaps()
    unregister_header()
    unregister_panels()
    unregister_operators()
    unregister_properties()
    unregister_preferences()


if __name__ == "__main__":
    register()
