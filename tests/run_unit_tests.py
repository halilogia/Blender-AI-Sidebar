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


def _build_suite(loader, targets):
    """Build either the full suite or a focused suite from file/module targets."""
    if not targets:
        start_dir = os.path.join(PROJECT_ROOT, "tests", "unit")
        return loader.discover(start_dir, pattern="test_*.py"), "all unit tests"

    suite = unittest.TestSuite()
    for target in targets:
        target_path = os.path.abspath(target)
        if os.path.isfile(target_path):
            suite.addTests(
                loader.discover(
                    os.path.dirname(target_path),
                    pattern=os.path.basename(target_path),
                )
            )
        else:
            suite.addTests(loader.loadTestsFromName(target))
    return suite, ", ".join(targets)


def main(argv=None):
    targets = list(argv if argv is not None else sys.argv[1:])
    loader = unittest.TestLoader()
    suite, label = _build_suite(loader, targets)
    print(f"\n=== RUNNING BLENDER AI SIDEBAR {label.upper()} ===")

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
