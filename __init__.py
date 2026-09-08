"""Blender AI Sidebar — Autonomous AI Agent & Grounding Copilot for Blender."""

bl_info = {
    "name": "Blender AI Sidebar",
    "author": "Halil Emre",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > AI Sidebar / View3D > Alt+Space (Command Bar)",
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
from .ui.uilist import register_uilist, unregister_uilist
from .ui.operators import register_operators, unregister_operators
from .ui.panel import register_panels, unregister_panels
from .ui.command_bar import register_command_bar, unregister_command_bar
from .ui.conversation_drawer import register_conversation_drawer, unregister_conversation_drawer
from .ui.keymap import register_keymaps, unregister_keymaps
from .ui.header import register_header, unregister_header
from .ui.timer_bridge import TimerBridge
from .adapter.blender_adapter import BlenderAdapter
from .tools.registry import ToolRegistry
from .tools.read_only.inspect_scene import InspectSceneTool
from .tools.read_only.inspect_selection import InspectSelectionTool
from .tools.read_only.inspect_object import InspectObjectTool
from .tools.read_only.inspect_material import InspectMaterialTool
from .tools.read_only.inspect_mesh import InspectMeshTool
from .agent.mock_provider import MockProvider
from .agent.dispatcher import ToolDispatcher
from .agent.runtime import AgentRuntime

_runtime: Optional[AgentRuntime] = None
_timer_bridge: Optional[TimerBridge] = None


def get_runtime() -> Optional[AgentRuntime]:
    """Retrieve the active extension agent runtime."""
    return _runtime


def get_timer_bridge() -> Optional[TimerBridge]:
    """Retrieve the active extension timer bridge."""
    return _timer_bridge


def register():
    """Register all extension components, tools, runtime, and timer bridge."""
    global _runtime, _timer_bridge

    # Idempotency guard: if already registered, unregister cleanly first
    if _runtime is not None or _timer_bridge is not None:
        unregister()

    # 1. UI Preferences, Properties, UIList, Operators, Panels, Floating Bars, Header & Keymaps
    register_preferences()
    register_properties()
    register_uilist()
    register_operators()
    register_panels()
    register_command_bar()
    register_conversation_drawer()
    register_header()
    register_keymaps()

    # 2. Tool Registry & Readers
    registry = ToolRegistry()
    registry.register(InspectSceneTool())
    registry.register(InspectSelectionTool())
    registry.register(InspectObjectTool())
    registry.register(InspectMaterialTool())
    registry.register(InspectMeshTool())

    # 3. Adapter & Dispatcher
    adapter = BlenderAdapter()
    dispatcher = ToolDispatcher(registry=registry, adapter=adapter)

    # 4. Mock Provider & Agent Runtime
    provider = MockProvider()
    _runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

    # 5. Timer Bridge for Async Event Loop
    _timer_bridge = TimerBridge(runtime=_runtime, event_queue=_runtime.event_queue)
    _timer_bridge.register()


def unregister():
    """Unregister all extension components and guarantee clean shutdown."""
    global _runtime, _timer_bridge

    # 1. Stop timer bridge
    if _timer_bridge is not None:
        _timer_bridge.unregister()
        _timer_bridge = None

    # 2. Shutdown runtime and background workers
    if _runtime is not None:
        _runtime.shutdown()
        _runtime = None

    # 3. Unregister UI & Keymaps
    unregister_keymaps()
    unregister_header()
    unregister_conversation_drawer()
    unregister_command_bar()
    unregister_panels()
    unregister_operators()
    unregister_uilist()
    unregister_properties()
    unregister_preferences()


if __name__ == "__main__":
    register()
