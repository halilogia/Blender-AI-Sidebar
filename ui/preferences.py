"""Addon preferences and configuration UI for Blender AI Sidebar.

Manages loading and saving to config.json, environment variable overrides,
and provides a secure UI for setting Base URL, API Key, Model, and Timeout.
"""

from pathlib import Path
from typing import Optional
import os
import bpy
from bpy.types import AddonPreferences, Operator
from bpy.props import StringProperty, IntProperty

from core.config import (
    Config,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    load_config,
    save_config,
    is_network_allowed,
    mask_api_key,
)


def get_addon_package() -> str:
    """Resolve addon package name reliably across test runners and extensions."""
    if __package__:
        parts = [p for p in __package__.split(".") if p and p != "ui"]
        if parts:
            return parts[0]
    return "blender_ai_sidebar"


def get_config_path() -> Path:
    """Resolve config.json path using Blender extension_path_user with fallback."""
    pkg = get_addon_package()
    if hasattr(bpy.utils, "extension_path_user"):
        try:
            p = bpy.utils.extension_path_user(pkg, create=True)
            if p:
                return Path(p) / "config.json"
        except (ValueError, Exception):
            pass

    config_dir = Path(bpy.utils.user_resource("CONFIG")) / "blender_ai_sidebar"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.json"


def get_effective_config() -> Config:
    """Retrieve the effective configuration respecting ENV > file > defaults."""
    cfg, _ = load_config(get_config_path())
    return cfg


def get_preferences(context=None) -> Optional["AISidebarPreferences"]:
    """Retrieve AI Sidebar addon preferences safely."""
    ctx = context or bpy.context
    pkg = get_addon_package()
    addon = ctx.preferences.addons.get(pkg)
    if addon and hasattr(addon, "preferences"):
        return addon.preferences
    return None


_is_updating_from_disk = False


def _on_preference_updated(self, context):
    """Callback when a preference field is edited by the user."""
    global _is_updating_from_disk
    if _is_updating_from_disk:
        return
    cfg = Config(
        base_url=self.base_url,
        api_key=self.api_key,
        model=self.model,
        timeout_seconds=float(self.timeout_seconds),
    )
    save_config(cfg, get_config_path())
    try:
        from .. import update_runtime_config
        update_runtime_config(cfg)
    except Exception:
        pass


class AI_SIDEBAR_OT_save_preferences(Operator):
    """Save current preferences to config.json"""

    bl_idname = "ai_sidebar.save_preferences"
    bl_label = "Save Preferences"
    bl_description = "Explicitly save preferences to config.json file"

    def execute(self, context):
        pkg = get_addon_package()
        addon_prefs = context.preferences.addons.get(pkg)
        if addon_prefs:
            prefs = addon_prefs.preferences
            cfg = Config(
                base_url=prefs.base_url,
                api_key=prefs.api_key,
                model=prefs.model,
                timeout_seconds=float(prefs.timeout_seconds),
            )
            save_config(cfg, get_config_path())
            self.report({"INFO"}, "Preferences saved to config.json.")
        return {"FINISHED"}


class AI_SIDEBAR_OT_reload_preferences(Operator):
    """Reload preferences from config.json and environment variables"""

    bl_idname = "ai_sidebar.reload_preferences"
    bl_label = "Reload Preferences"
    bl_description = "Reload configuration from file and environment variables"

    def execute(self, context):
        pkg = get_addon_package()
        addon_prefs = context.preferences.addons.get(pkg)
        if addon_prefs:
            prefs = addon_prefs.preferences
            prefs.load_from_disk()
            self.report({"INFO"}, "Preferences reloaded.")
        return {"FINISHED"}


class AISidebarPreferences(AddonPreferences):
    """Add-on preferences for AI Sidebar."""

    bl_idname = get_addon_package()

    base_url: StringProperty(
        name="Base URL",
        description="OpenAI-compatible server endpoint (e.g., http://localhost:11434/v1 or https://openrouter.ai/api/v1)",
        default=DEFAULT_BASE_URL,
        update=_on_preference_updated,
    )

    api_key: StringProperty(
        name="API Key",
        description="API Key for the provider (kept out of .blend files, masked in UI)",
        default="",
        subtype="PASSWORD",
        options={"SKIP_SAVE"},
        update=_on_preference_updated,
    )

    model: StringProperty(
        name="Model",
        description="Target model identifier (e.g., llama3.1:latest, gpt-4o)",
        default=DEFAULT_MODEL,
        update=_on_preference_updated,
    )

    timeout_seconds: IntProperty(
        name="Timeout (seconds)",
        description="Network request timeout in seconds",
        default=int(DEFAULT_TIMEOUT_SECONDS),
        min=5,
        max=300,
        update=_on_preference_updated,
    )

    def load_from_disk(self):
        """Populate preference properties from file and environment."""
        global _is_updating_from_disk
        _is_updating_from_disk = True
        try:
            cfg, _ = load_config(get_config_path())
            self.base_url = cfg.base_url
            self.api_key = cfg.api_key
            self.model = cfg.model
            self.timeout_seconds = int(cfg.timeout_seconds)
        finally:
            _is_updating_from_disk = False

    def draw(self, context):
        layout = self.layout

        # Online access validation
        online_access = getattr(bpy.app, "online_access", True)
        allowed, msg = is_network_allowed(self.base_url, online_access)
        if not allowed:
            warning_box = layout.box()
            warning_box.alert = True
            warning_box.label(text=msg, icon="ERROR")

        # Environment variable overrides indicator
        env_url = os.environ.get("BLENDER_AI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        env_key = os.environ.get("BLENDER_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        env_model = os.environ.get("BLENDER_AI_MODEL")

        if env_url or env_key or env_model:
            info_box = layout.box()
            info_box.label(text="Some settings are overridden by environment variables:", icon="INFO")
            if env_url:
                info_box.label(text=f"• Base URL overridden: {env_url}")
            if env_key:
                info_box.label(text=f"• API Key overridden: {mask_api_key(env_key)}")
            if env_model:
                info_box.label(text=f"• Model overridden: {env_model}")

        # Input fields
        col = layout.column(align=True)
        col.prop(self, "base_url")
        col.prop(self, "api_key")
        col.prop(self, "model")
        col.prop(self, "timeout_seconds")

        # Action buttons
        row = layout.row(align=True)
        row.operator("ai_sidebar.save_preferences", icon="FILE_TICK")
        row.operator("ai_sidebar.reload_preferences", icon="FILE_REFRESH")


PREF_CLASSES = (
    AI_SIDEBAR_OT_save_preferences,
    AI_SIDEBAR_OT_reload_preferences,
    AISidebarPreferences,
)


def register_preferences():
    """Register preference classes."""
    for cls in PREF_CLASSES:
        try:
            bpy.utils.register_class(cls)
        except (ValueError, RuntimeError):
            pass


def unregister_preferences():
    """Unregister preference classes."""
    for cls in reversed(PREF_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError):
            pass
