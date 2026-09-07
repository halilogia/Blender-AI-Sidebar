"""Headless integration test for extension loading, registration, and manifest verification."""

import sys
import os
import tomllib

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy


def test_manifest():
    """Verify blender_manifest.toml existence and fields."""
    manifest_path = os.path.join(PROJECT_ROOT, "blender_manifest.toml")
    assert os.path.isfile(manifest_path), f"Manifest not found at {manifest_path}"

    with open(manifest_path, "rb") as fh:
        data = tomllib.load(fh)

    assert data.get("id") == "blender_ai_sidebar", f"Invalid id: {data.get('id')}"
    assert data.get("type") == "add-on", f"Invalid type: {data.get('type')}"
    assert data.get("blender_version_min") == "4.2.0", f"Invalid min version: {data.get('blender_version_min')}"
    assert data.get("schema_version") == "1.0.0", f"Invalid schema version: {data.get('schema_version')}"
    print("[PASS] blender_manifest.toml is valid and conforms to Blender 5.2 extension schema.")


def test_registration_cycle():
    """Test register and unregister cycle in live Blender 5.2 environment."""
    import importlib.util
    init_path = os.path.join(PROJECT_ROOT, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        "blender_ai_sidebar",
        init_path,
        submodule_search_locations=[PROJECT_ROOT],
    )
    extension_mod = importlib.util.module_from_spec(spec)
    sys.modules["blender_ai_sidebar"] = extension_mod
    spec.loader.exec_module(extension_mod)

    # 1. Register
    extension_mod.register()

    assert hasattr(bpy.types.WindowManager, "ai_sidebar"), "WindowManager.ai_sidebar was not registered!"
    assert hasattr(bpy.types, "AISIDEBAR_PT_main_panel"), "AISIDEBAR_PT_main_panel was not registered!"

    # Verify window manager property access
    wm = bpy.context.window_manager
    assert wm.ai_sidebar.agent_status == "IDLE", f"Unexpected status: {wm.ai_sidebar.agent_status}"
    assert wm.ai_sidebar.last_result_summary == "Ready", f"Unexpected summary: {wm.ai_sidebar.last_result_summary}"
    print("[PASS] Extension registered cleanly and WindowManager properties are active.")

    # 2. Unregister
    extension_mod.unregister()

    assert not hasattr(bpy.types.WindowManager, "ai_sidebar"), "WindowManager.ai_sidebar was not cleaned up!"
    assert not hasattr(bpy.types, "AISIDEBAR_PT_main_panel"), "AISIDEBAR_PT_main_panel was not cleaned up!"
    print("[PASS] Extension unregistered cleanly with zero residual properties or classes.")


if __name__ == "__main__":
    try:
        print("\n=== STARTING PHASE 1 EXTENSION LOAD TEST ===")
        test_manifest()
        test_registration_cycle()
        print("=== PHASE 1 EXTENSION LOAD TEST COMPLETED SUCCESSFULLY ===\n")
        sys.exit(0)
    except AssertionError as err:
        print(f"\n[FAIL] Assertion error: {err}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected exception: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
