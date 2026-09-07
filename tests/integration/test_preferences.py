"""Integration tests for M2.1: Preferences, Config Management & Security Boundary in Blender 5.2.1 LTS.

Verifies the 10 acceptance criteria:
1. Extension enable & preference registration
2. Preferences UI drawing and layout
3. base_url saved to config.json
4. model saved to config.json
5. api_key saved to config.json
6. config reload works
7. ENV override works with proper priority
8. api_key masked in UI (PASSWORD subtype) and logs
9. .blend file has zero config/key pollution
10. Clean unregistration and lifecycle
"""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

init_path = os.path.join(PROJECT_ROOT, "__init__.py")
spec = importlib.util.spec_from_file_location(
    "blender_ai_sidebar",
    init_path,
    submodule_search_locations=[PROJECT_ROOT],
)
blender_ai_sidebar = importlib.util.module_from_spec(spec)
sys.modules["blender_ai_sidebar"] = blender_ai_sidebar
spec.loader.exec_module(blender_ai_sidebar)

from blender_ai_sidebar.core.config import (
    Config,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    load_config,
    save_config,
    mask_api_key,
    is_network_allowed,
)
from blender_ai_sidebar.ui.preferences import (
    AISidebarPreferences,
    get_config_path,
    get_preferences,
    get_addon_package,
)


class DummyUILayout:
    """Mock Blender layout for headless draw testing."""

    def __init__(self):
        self.labels = []
        self.props = []
        self.operators = []
        self.alert = False

    def box(self):
        return self

    def column(self, align=False):
        return self

    def row(self, align=False):
        return self

    def label(self, text="", icon="NONE"):
        self.labels.append((text, icon))

    def prop(self, data, prop_name, text=""):
        self.props.append((data, prop_name, text))

    def operator(self, op_name, text="", icon="NONE"):
        self.operators.append((op_name, text, icon))
        return self


def run_tests():
    print("\n=== STARTING M2.1 PREFERENCES & CONFIG INTEGRATION TESTS ===")

    temp_dir = tempfile.TemporaryDirectory()
    test_config_path = Path(temp_dir.name) / "test_config.json"

    # Save original config path function
    import blender_ai_sidebar.ui.preferences as pref_mod
    original_get_config_path = pref_mod.get_config_path
    pref_mod.get_config_path = lambda: test_config_path

    # Clean env vars
    clean_env_keys = [
        "BLENDER_AI_API_KEY", "BLENDER_AI_BASE_URL",
        "BLENDER_AI_MODEL", "BLENDER_AI_TIMEOUT",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ]
    old_env = {}
    for k in clean_env_keys:
        if k in os.environ:
            old_env[k] = os.environ.pop(k)

    try:
        # 1. Extension Enable & Preference Registration
        print("Test 1: Extension register and preference class availability...")
        blender_ai_sidebar.register()
        assert any(c.__name__ == "AISidebarPreferences" for c in bpy.types.AddonPreferences.__subclasses__()), "AISidebarPreferences class not registered!"
        assert hasattr(bpy.types, "AI_SIDEBAR_OT_save_preferences"), "save_preferences operator not in bpy.types!"
        assert hasattr(bpy.types, "AI_SIDEBAR_OT_reload_preferences"), "reload_preferences operator not in bpy.types!"
        print("[PASS] Test 1: Preferences and operators registered cleanly.")

        # Create preferences instance for testing
        pkg = get_addon_package()
        # In Blender headless/script mode, ensure addon entry exists
        if pkg not in bpy.context.preferences.addons:
            addon = bpy.context.preferences.addons.new()
            addon.module = pkg
        else:
            addon = bpy.context.preferences.addons[pkg]
        prefs = addon.preferences
        assert prefs is not None, "Addon preferences could not be obtained!"

        # 2. Preferences UI Drawing & Properties
        print("Test 2: Preferences UI drawing and layout verification...")
        layout = DummyUILayout()
        prefs.draw = AISidebarPreferences.draw.__get__(prefs, AISidebarPreferences)
        prefs.layout = layout
        prefs.draw(bpy.context)

        drawn_props = [p[1] for p in layout.props]
        assert "base_url" in drawn_props, "base_url property missing from UI draw!"
        assert "api_key" in drawn_props, "api_key property missing from UI draw!"
        assert "model" in drawn_props, "model property missing from UI draw!"
        assert "timeout_seconds" in drawn_props, "timeout_seconds property missing from UI draw!"

        drawn_ops = [op[0] for op in layout.operators]
        assert "ai_sidebar.save_preferences" in drawn_ops, "Save operator missing from UI!"
        assert "ai_sidebar.reload_preferences" in drawn_ops, "Reload operator missing from UI!"
        print("[PASS] Test 2: Preferences UI layout draws all 4 properties and 2 operators.")

        # 3, 4, 5. Property modification saves to config.json
        print("Test 3, 4, 5: Property update persistence to config.json...")
        prefs.base_url = "http://localhost:1234/v1"
        prefs.model = "qwen2.5-coder:7b"
        prefs.api_key = "sk-live-testkey-12345"
        prefs.timeout_seconds = 45

        assert test_config_path.is_file(), f"Config file not created at {test_config_path}"
        with open(test_config_path, "r", encoding="utf-8") as f:
            saved_json = json.load(f)

        assert saved_json.get("base_url") == "http://localhost:1234/v1", f"Wrong base_url: {saved_json.get('base_url')}"
        assert saved_json.get("model") == "qwen2.5-coder:7b", f"Wrong model: {saved_json.get('model')}"
        assert saved_json.get("api_key") == "sk-live-testkey-12345", f"Wrong api_key: {saved_json.get('api_key')}"
        assert saved_json.get("timeout_seconds") == 45.0, f"Wrong timeout: {saved_json.get('timeout_seconds')}"
        print("[PASS] Test 3, 4, 5: base_url, model, api_key, timeout persisted to config.json.")

        # 6. Config Reload Operator
        print("Test 6: Reload preferences from disk...")
        # External modification
        with open(test_config_path, "w", encoding="utf-8") as f:
            json.dump({
                "version": 1,
                "base_url": "http://localhost:11434/v1",
                "model": "mistral:latest",
                "api_key": "sk-reloaded-key-999",
                "timeout_seconds": 60.0,
            }, f)

        # Trigger reload operator
        bpy.ops.ai_sidebar.reload_preferences()
        assert prefs.base_url == "http://localhost:11434/v1", f"Reload failed for base_url: {prefs.base_url}"
        assert prefs.model == "mistral:latest", f"Reload failed for model: {prefs.model}"
        assert prefs.api_key == "sk-reloaded-key-999", f"Reload failed for api_key: {prefs.api_key}"
        assert prefs.timeout_seconds == 60, f"Reload failed for timeout: {prefs.timeout_seconds}"
        print("[PASS] Test 6: Reload preferences operator successfully loaded updated disk state.")

        # 7. Environment Variable Overrides
        print("Test 7: Environment variable override priority...")
        os.environ["BLENDER_AI_BASE_URL"] = "https://openrouter.ai/api/v1"
        os.environ["BLENDER_AI_API_KEY"] = "sk-or-v1-env-override"
        os.environ["BLENDER_AI_MODEL"] = "anthropic/claude-3.5-sonnet"
        os.environ["BLENDER_AI_TIMEOUT"] = "90"

        # Reload preferences respecting ENV
        prefs.load_from_disk()
        assert prefs.base_url == "https://openrouter.ai/api/v1", f"ENV override failed for base_url: {prefs.base_url}"
        assert prefs.api_key == "sk-or-v1-env-override", f"ENV override failed for api_key: {prefs.api_key}"
        assert prefs.model == "anthropic/claude-3.5-sonnet", f"ENV override failed for model: {prefs.model}"
        assert prefs.timeout_seconds == 90, f"ENV override failed for timeout: {prefs.timeout_seconds}"

        # Test UI draws ENV override warning box with masked key
        layout_env = DummyUILayout()
        prefs.layout = layout_env
        prefs.draw(bpy.context)
        override_labels = [l[0] for l in layout_env.labels]
        assert any("Some settings are overridden by environment variables" in l for l in override_labels)
        assert any("sk-o...ride" in l for l in override_labels), "Masked API key not found in override UI labels!"
        assert not any("sk-or-v1-env-override" in l for l in override_labels), "Plaintext API key leaked in UI labels!"
        print("[PASS] Test 7: ENV variables take precedence and UI displays masked notification.")

        # 8. Security & Masking
        print("Test 8: API key UI masking and RNA attributes...")
        # Check RNA property definition
        rna_prop = AISidebarPreferences.bl_rna.properties.get("api_key")
        assert rna_prop is not None, "api_key RNA property not found!"
        assert rna_prop.subtype == "PASSWORD", f"api_key subtype is not PASSWORD: {rna_prop.subtype}"
        # Test masking helper
        assert mask_api_key("sk-abcdefgh12345678") == "sk-a...5678"
        assert mask_api_key("") == ""
        assert mask_api_key("short") == "********"
        print("[PASS] Test 8: api_key is configured as PASSWORD subtype and masked safely.")

        # 9. Zero Config in .blend / Scene / WindowManager
        print("Test 9: Verify zero config/key pollution in Blender scene and .blend structures...")
        # Check Scene properties
        scene = bpy.context.scene
        assert not hasattr(scene, "api_key"), "api_key leaked onto Scene!"
        assert not hasattr(scene, "base_url"), "base_url leaked onto Scene!"
        assert "api_key" not in scene.keys(), "api_key leaked into Scene custom properties!"

        # Check WindowManager AI Sidebar properties
        wm_sidebar = bpy.context.window_manager.ai_sidebar
        assert not hasattr(wm_sidebar, "api_key"), "api_key leaked onto WindowManager.ai_sidebar!"
        assert not hasattr(wm_sidebar, "base_url"), "base_url leaked onto WindowManager.ai_sidebar!"

        # Save to temporary .blend file and check content
        blend_path = os.path.join(temp_dir.name, "test_clean_scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        with open(blend_path, "rb") as bf:
            blend_bytes = bf.read()
        assert b"sk-or-v1-env-override" not in blend_bytes, "CRITICAL: API key leaked into .blend file binary!"
        assert b"sk-reloaded-key-999" not in blend_bytes, "CRITICAL: Reloaded API key leaked into .blend file binary!"
        print("[PASS] Test 9: Zero config or API key leaked to Scene, WindowManager, or saved .blend file.")

        # 10. Clean Unregistration
        print("Test 10: Extension unregistration and lifecycle cleanup...")
        blender_ai_sidebar.unregister()
        assert not hasattr(bpy.types, "AI_SIDEBAR_OT_save_preferences"), "save_preferences operator still in bpy.types!"
        assert not hasattr(bpy.types, "AI_SIDEBAR_OT_reload_preferences"), "reload_preferences operator still in bpy.types!"
        try:
            bpy.utils.unregister_class(AISidebarPreferences)
            assert False, "AISidebarPreferences was not unregistered!"
        except RuntimeError:
            pass  # Expected: already unregistered
        print("[PASS] Test 10: Preferences cleanly unregistered without residuals.")

    finally:
        pref_mod.get_config_path = original_get_config_path
        temp_dir.cleanup()
        for k in clean_env_keys:
            if k in os.environ:
                del os.environ[k]
        os.environ.update(old_env)

    print("\n=== ALL M2.1 PREFERENCES & CONFIG INTEGRATION TESTS PASSED (10/10) ===\n")


if __name__ == "__main__":
    try:
        run_tests()
        sys.exit(0)
    except AssertionError as err:
        print(f"\n[FAIL] Assertion error: {err}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected exception: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
