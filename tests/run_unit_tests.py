"""Pure Python test runner for unit tests.

Runs without requiring Blender or any external dependencies.
Usage:
    python tests/run_unit_tests.py
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def main():
    print("\n=== RUNNING BLENDER AI SIDEBAR PURE PYTHON UNIT TESTS ===")
    loader = unittest.TestLoader()
    start_dir = os.path.join(PROJECT_ROOT, "tests", "unit")
    suite = loader.discover(start_dir, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if result.wasSuccessful():
        print("=== ALL UNIT TESTS PASSED SUCCESSFULLY ===\n")
        return 0
    else:
        print(f"=== UNIT TESTS FAILED: {len(result.failures)} failures, {len(result.errors)} errors ===\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
