"""Master integration test runner executing all Blender integration tests in sequence."""

import subprocess
import sys
import os

TESTS = [
    "tests/integration/test_extension_load.py",
    "tests/integration/test_grounding_tools.py",
    "tests/integration/test_mock_agent_flow.py",
    "tests/integration/test_async_blender_flow.py",
    "tests/integration/test_ui_integration.py",
    "tests/integration/test_ui_hardening.py",
    "tests/integration/test_m1_acceptance.py",
    "tests/integration/test_preferences.py",
    "tests/integration/test_provider_roundtrip.py",
    "tests/integration/test_gpu_overlay.py",
    "tests/integration/test_mutations_and_undo.py",
    "tests/integration/test_approval_integration.py",
    "tests/integration/test_verification_integration.py",
    "tests/integration/test_material_mutations.py",
    "tests/integration/test_viewport_capture.py",
    "tests/integration/test_multimodal_integration.py",
    "tests/integration/test_visual_verification_integration.py",
    "tests/integration/test_session_persistence.py",
    "tests/integration/test_camera_integration.py",
    "tests/integration/test_light_integration.py",
    "tests/integration/test_geometry_quality_integration.py",
    "tests/integration/test_duplicate_integration.py",
    "tests/integration/test_agentic_acceptance.py",
]

BLENDER_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"

def run_all():
    print("\n========================================================")
    print("   RUNNING ALL BLENDER INTEGRATION & ACCEPTANCE TESTS   ")
    print("========================================================\n")

    failed = []
    for test in TESTS:
        print(f"\n>>> Running {test}...")
        cmd = [BLENDER_PATH, "--background", "--python", test]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
            failed.append(test)

    print("\n========================================================")
    if not failed:
        print(f"   ALL BLENDER INTEGRATION TESTS PASSED ({len(TESTS)}/{len(TESTS)} SUITES)    ")
        print("========================================================\n")
        return 0
    else:
        print(f"   FAILED SUITES ({len(failed)}): {failed}")
        print("========================================================\n")
        return 1

if __name__ == "__main__":
    sys.exit(run_all())
