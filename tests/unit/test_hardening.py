"""Phase 8: Unit-level hardening tests.

Tests:
1. Runtime isolation (no bpy in core/agent/tools, no adapter in ui, etc.)
2. Security boundary (no eval, exec, subprocess, network calls)
3. JSON serialization hardening (special floats, None, nested data)
4. History memory bound enforcement
5. Concurrent prompt submission rejection
"""

import ast
import json
import math
import os
import unittest
from unittest.mock import MagicMock

from core.types import RiskLevel, ToolError, ToolResult
from agent.models import ToolCall, ProviderResponse, AgentResult
from agent.state_machine import AgentState, AgentStateMachine
from agent.history import RuntimeHistory, HistoryKind, HistoryItem
from agent.runtime import AgentRuntime
from agent.dispatcher import ToolDispatcher
from tools.registry import ToolRegistry


class TestRuntimeIsolationAndSecurity(unittest.TestCase):
    """Verify architectural boundaries and security constraints."""

    def setUp(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _get_python_files(self, rel_dir: str):
        target_dir = os.path.join(self.project_root, rel_dir)
        py_files = []
        for root, _, files in os.walk(target_dir):
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))
        return py_files

    def test_no_bpy_in_pure_python_layers(self):
        """core, agent, and tools must not import bpy."""
        for folder in ["core", "agent", "tools"]:
            files = self._get_python_files(folder)
            for file_path in files:
                with open(file_path, "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=file_path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotEqual(
                                alias.name, "bpy",
                                f"Forbidden 'import bpy' in {file_path}"
                            )
                    elif isinstance(node, ast.ImportFrom):
                        self.assertNotEqual(
                            node.module, "bpy",
                            f"Forbidden 'from bpy ...' in {file_path}"
                        )
                        if node.module:
                            self.assertFalse(
                                node.module.startswith("bpy."),
                                f"Forbidden 'from bpy... in {file_path}"
                            )

    def test_ui_has_no_direct_adapter_or_tool_access(self):
        """ui layer must not import adapter or tools."""
        files = self._get_python_files("ui")
        for file_path in files:
            with open(file_path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=file_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        "adapter" in node.module,
                        f"Forbidden adapter import in {file_path}"
                    )
                    self.assertFalse(
                        "tools" in node.module,
                        f"Forbidden tools import in {file_path}"
                    )

    def test_security_forbidden_calls(self):
        """Entire codebase must not use eval, exec, or subprocess; networking restricted to http_client."""
        forbidden_calls = {"eval", "exec"}
        forbidden_modules = {"subprocess", "urllib", "requests", "http.client", "socket"}

        for folder in ["core", "agent", "tools", "adapter", "ui"]:
            files = self._get_python_files(folder)
            for file_path in files:
                # Designated transport and launcher modules
                norm_path = file_path.replace("\\", "/")
                is_http_client = norm_path.endswith("agent/http_client.py")
                is_web_server = norm_path.endswith("core/web_server.py")
                is_web_launcher = norm_path.endswith("ui/web_launcher.py")

                with open(file_path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                    tree = ast.parse(content, filename=file_path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            self.assertNotIn(
                                node.func.id, forbidden_calls,
                                f"Forbidden call '{node.func.id}' in {file_path}"
                            )
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        mod = getattr(node, "module", None) or getattr(node, "names", [None])[0].name
                        base_mod = mod.split(".")[0]
                        if (is_http_client or is_web_server) and base_mod in {"urllib", "http", "socket", "ssl", "socketserver"}:
                            continue
                        if is_web_launcher and base_mod in {"subprocess", "webbrowser"}:
                            continue
                        self.assertNotIn(
                            base_mod, forbidden_modules,
                            f"Forbidden module '{base_mod}' in {file_path}"
                        )


class TestJsonSerializationHardening(unittest.TestCase):
    """Verify JSON serialization robustly handles edge-case values."""

    def test_tool_result_json_serialization(self):
        data = {
            "none_val": None,
            "bool_true": True,
            "bool_false": False,
            "int_val": 42,
            "negative_zero": -0.0,
            "float_val": 3.14159,
            "empty_list": [],
            "empty_dict": {},
            "nested": {
                "items": [1, 2, "str", None],
                "map": {"a": True, "b": []}
            }
        }
        res = ToolResult.ok(tool="test_tool", data=data)
        serialized = json.dumps(res.to_dict())
        loaded = json.loads(serialized)
        self.assertTrue(loaded["success"])
        self.assertEqual(loaded["data"]["none_val"], None)
        self.assertEqual(loaded["data"]["bool_true"], True)
        self.assertEqual(loaded["data"]["empty_list"], [])

    def test_tool_error_json_serialization(self):
        error_res = ToolResult.fail(
            tool="test_tool",
            error_type="OBJECT_NOT_FOUND",
            message="Item does not exist",
            details={"key_b": 2, "key_a": 1, "nested": None}
        )
        serialized = json.dumps(error_res.to_dict())
        loaded = json.loads(serialized)
        self.assertFalse(loaded["success"])
        self.assertEqual(loaded["error"]["type"], "OBJECT_NOT_FOUND")
        self.assertEqual(list(loaded["error"]["details"].keys()), ["key_a", "key_b", "nested"])


class TestHistoryMemoryPolicy(unittest.TestCase):
    """Verify history bounding behavior."""

    def test_history_cap_at_max_items(self):
        history = RuntimeHistory(max_items=5)
        for i in range(10):
            history.add(
                item_id=f"item_{i}",
                turn_id=f"turn_{i}",
                kind=HistoryKind.USER,
                title=f"Title {i}",
            )

        self.assertEqual(len(history.items), 5)
        self.assertEqual(history.items[0].item_id, "item_5")
        self.assertEqual(history.items[-1].item_id, "item_9")


class TestRuntimeConcurrencyHardening(unittest.TestCase):
    """Verify runtime protection against concurrent requests."""

    def test_concurrent_submit_rejected(self):
        mock_provider = MagicMock()
        mock_dispatcher = MagicMock()
        mock_worker = MagicMock()
        mock_worker.is_running = True

        runtime = AgentRuntime(
            provider=mock_provider,
            dispatcher=mock_dispatcher,
            worker=mock_worker,
        )

        # First submit -> status moves to PROCESSING
        runtime.submit_prompt("first prompt")
        self.assertEqual(runtime.current_state, AgentState.PROCESSING)

        # Second submit while PROCESSING must raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            runtime.submit_prompt("second prompt")
        self.assertIn("Active turn in progress", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
