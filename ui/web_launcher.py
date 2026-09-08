"""Launcher and window manager for Blender AI Sidebar Embedded Web UI."""

import os
import subprocess
import sys
import tempfile
import webbrowser
from typing import Optional
import bpy
from bpy.types import Operator

from core.web_server import LocalWebServer

_web_server: Optional[LocalWebServer] = None
_edge_process: Optional[subprocess.Popen] = None


def get_web_server() -> Optional[LocalWebServer]:
    """Retrieve active local web server instance."""
    return _web_server


def find_edge_path() -> Optional[str]:
    """Locate Microsoft Edge executable on Windows."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def start_web_server(runtime=None) -> LocalWebServer:
    """Initialize and start the local HTTP + SSE bridge server."""
    global _web_server
    if _web_server is None:
        _web_server = LocalWebServer()
        _web_server.start()

    if runtime:
        def on_prompt(prompt_text: str):
            # Queue prompt to AgentRuntime
            runtime.submit_prompt(prompt_text)

        def on_cancel():
            runtime.cancel_current_turn()

        def on_status():
            return {
                "agent_status": runtime.current_state.value,
                "current_action": getattr(runtime, "current_action", "Ready"),
            }

        _web_server.set_prompt_callback(on_prompt)
        _web_server.set_cancel_callback(on_cancel)
        _web_server.set_status_provider(on_status)

    return _web_server


def stop_web_server() -> None:
    """Stop the web server and clean up processes."""
    global _web_server, _edge_process
    if _edge_process:
        try:
            _edge_process.terminate()
        except Exception:
            pass
        _edge_process = None

    if _web_server:
        _web_server.stop()
        _web_server = None


def open_web_window(runtime=None) -> str:
    """Launch the Web UI in isolated Edge app mode or default browser."""
    global _edge_process
    server = start_web_server(runtime)
    url = server.app_url

    edge_exe = find_edge_path()
    if edge_exe and sys.platform == "win32":
        # Create dedicated isolated profile directory for the app window
        profile_dir = os.path.join(tempfile.gettempdir(), "blender_ai_edge_profile")
        os.makedirs(profile_dir, exist_ok=True)

        args = [
            edge_exe,
            f"--app={url}",
            "--window-size=500,680",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-default-apps",
            "--disable-extensions",
        ]
        try:
            _edge_process = subprocess.Popen(args)
            return url
        except Exception:
            pass

    # Fallback to default browser
    webbrowser.open(url)
    return url


class AISIDEBAR_OT_open_web_ui(Operator):
    """Launch the modern floating Web UI for Blender AI Copilot."""

    bl_idname = "ai_sidebar.open_web_ui"
    bl_label = "Open Web UI"
    bl_description = "Launch the modern floating Web UI"

    def execute(self, context):
        from .. import get_runtime
        runtime = get_runtime()
        url = open_web_window(runtime)
        self.report({"INFO"}, f"Blender AI Web UI opened at {url}")
        return {"FINISHED"}


CLASSES = (
    AISIDEBAR_OT_open_web_ui,
)


def register_web_launcher():
    """Register web launcher operator."""
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_web_launcher():
    """Unregister web launcher operator."""
    stop_web_server()
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError):
            pass
