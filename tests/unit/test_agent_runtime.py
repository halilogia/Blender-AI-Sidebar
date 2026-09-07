"""Unit tests for Phase 5 Agent Runtime, State Machine, MockProvider, and Dispatcher.

Runs in pure Python without Blender.
"""

import unittest
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool
from tools.registry import ToolRegistry
from agent.models import ToolCall, ProviderResponse, AgentResult
from agent.state_machine import AgentState, AgentStateMachine, InvalidStateTransitionError
from agent.mock_provider import MockProvider
from agent.dispatcher import ToolDispatcher
from agent.runtime import AgentRuntime


class DummySceneTool(BaseTool):
    name = "inspect_scene"
    description = "Mock scene tool"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter, **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, {"counts": {"total": 4}, "scene_name": "TestScene"})


class DummyObjectTool(BaseTool):
    name = "inspect_object"
    description = "Mock object tool"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter, **kwargs) -> ToolResult:
        name = kwargs.get("name")
        if name == "Ghost":
            return ToolResult.fail(self.name, "OBJECT_NOT_FOUND", f"Object '{name}' not found.")
        return ToolResult.ok(self.name, {"name": name, "type": "MESH"})


class DummyFailingTool(BaseTool):
    name = "failing_tool"
    description = "A tool that throws an unexpected exception"
    input_schema = {"type": "object"}
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter, **kwargs) -> ToolResult:
        raise RuntimeError("Unexpected boom!")


class TestStateMachine(unittest.TestCase):
    """Test deterministic state transitions and illegal transition enforcement."""

    def test_valid_transitions(self):
        sm = AgentStateMachine()
        self.assertEqual(sm.current_state, AgentState.IDLE)

        sm.transition_to(AgentState.PROCESSING)
        self.assertEqual(sm.current_state, AgentState.PROCESSING)

        sm.transition_to(AgentState.EXECUTING_TOOL)
        self.assertEqual(sm.current_state, AgentState.EXECUTING_TOOL)

        sm.transition_to(AgentState.PROCESSING)
        self.assertEqual(sm.current_state, AgentState.PROCESSING)

        sm.transition_to(AgentState.IDLE)
        self.assertEqual(sm.current_state, AgentState.IDLE)

    def test_illegal_transition_raises(self):
        sm = AgentStateMachine()
        # Direct IDLE -> EXECUTING_TOOL is forbidden
        with self.assertRaises(InvalidStateTransitionError):
            sm.transition_to(AgentState.EXECUTING_TOOL)

    def test_error_transition_and_reset(self):
        sm = AgentStateMachine()
        sm.transition_to(AgentState.PROCESSING)
        sm.transition_to(AgentState.ERROR)
        self.assertEqual(sm.current_state, AgentState.ERROR)

        sm.reset()
        self.assertEqual(sm.current_state, AgentState.IDLE)


class TestDispatcher(unittest.TestCase):
    """Test ToolDispatcher validation and execution."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(DummySceneTool())
        self.registry.register(DummyObjectTool())
        self.registry.register(DummyFailingTool())
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=None)

    def test_valid_tool_dispatch(self):
        tc = ToolCall(call_id="call_1", tool_name="inspect_scene", arguments={})
        res = self.dispatcher.dispatch(tc)
        self.assertTrue(res.success)
        self.assertEqual(res.data["scene_name"], "TestScene")

    def test_unknown_tool_dispatch(self):
        tc = ToolCall(call_id="call_2", tool_name="unregistered_tool", arguments={})
        res = self.dispatcher.dispatch(tc)
        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "TOOL_NOT_FOUND")

    def test_missing_required_argument(self):
        tc = ToolCall(call_id="call_3", tool_name="inspect_object", arguments={})
        res = self.dispatcher.dispatch(tc)
        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("Missing required argument", res.error.message)

    def test_unexpected_argument_rejected(self):
        tc = ToolCall(call_id="call_4", tool_name="inspect_scene", arguments={"extra": 123})
        res = self.dispatcher.dispatch(tc)
        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "INVALID_ARGUMENT")
        self.assertIn("Unexpected argument", res.error.message)

    def test_unhandled_tool_exception_caught(self):
        tc = ToolCall(call_id="call_5", tool_name="failing_tool", arguments={})
        res = self.dispatcher.dispatch(tc)
        self.assertFalse(res.success)
        self.assertEqual(res.error.type, "DISPATCHER_ERROR")
        self.assertIn("Unexpected boom!", res.error.message)


class TestAgentRuntime(unittest.TestCase):
    """Test full agent loop with MockProvider and Dispatcher."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(DummySceneTool())
        self.registry.register(DummyObjectTool())
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=None)
        self.provider = MockProvider()
        self.runtime = AgentRuntime(provider=self.provider, dispatcher=self.dispatcher)

    def test_direct_unhandled_prompt(self):
        res = self.runtime.run("Merhaba dünya")
        self.assertEqual(res.state, "IDLE")
        self.assertEqual(len(res.tool_results), 0)
        self.assertIn("Anlaşılmayan istek", res.final_text)

    def test_successful_tool_cycle(self):
        res = self.runtime.run("Mevcut sahneyi incele")
        self.assertEqual(res.state, "IDLE")
        self.assertEqual(len(res.tool_results), 1)
        self.assertTrue(res.tool_results[0].success)
        self.assertEqual(res.tool_results[0].tool, "inspect_scene")
        self.assertIn("Sahne incelemesi tamamlandı. Toplam 4 nesne bulundu.", res.final_text)

    def test_tool_with_arguments_cycle(self):
        res = self.runtime.run("Cube'u incele")
        self.assertEqual(res.state, "IDLE")
        self.assertEqual(len(res.tool_results), 1)
        self.assertTrue(res.tool_results[0].success)
        self.assertEqual(res.tool_results[0].data["name"], "Cube")
        self.assertIn("Obje incelemesi tamamlandı: Cube (Tip: MESH).", res.final_text)

    def test_tool_failure_enters_error_state(self):
        # Create a mock provider that generates a call to "Ghost"
        class FailingCallProvider(MockProvider):
            def generate(self, prompt, tool_results=None):
                if tool_results:
                    return super().generate(prompt, tool_results)
                return ProviderResponse(
                    assistant_text="Ghost aranıyor...",
                    tool_calls=[ToolCall(call_id="call_ghost", tool_name="inspect_object", arguments={"name": "Ghost"})],
                    is_final=False,
                )

        failing_runtime = AgentRuntime(provider=FailingCallProvider(), dispatcher=self.dispatcher)
        res = failing_runtime.run("Ghost'u incele")

        self.assertEqual(res.state, "ERROR")
        self.assertEqual(len(res.tool_results), 1)
        self.assertFalse(res.tool_results[0].success)
        self.assertEqual(res.tool_results[0].error.type, "OBJECT_NOT_FOUND")
        self.assertIn("Araç hatası", res.final_text)

    def test_sequential_tool_calls(self):
        # "Tam inceleme" triggers [inspect_scene, inspect_selection]
        # Register a mock selection tool
        class DummySelectionTool(BaseTool):
            name = "inspect_selection"
            description = "Mock selection"
            input_schema = {"type": "object"}
            risk_level = RiskLevel.READ_ONLY

            def execute(self, adapter, **kwargs):
                return ToolResult.ok(self.name, {"active_object": "Cube", "selection_count": 1})

        self.registry.register(DummySelectionTool())

        res = self.runtime.run("Tam inceleme")
        self.assertEqual(res.state, "IDLE")
        self.assertEqual(len(res.tool_results), 2)
        self.assertEqual(res.tool_results[0].tool, "inspect_scene")
        self.assertEqual(res.tool_results[1].tool, "inspect_selection")
        self.assertIn("Sahne incelemesi tamamlandı", res.final_text)
        self.assertIn("Seçim incelemesi tamamlandı", res.final_text)


if __name__ == "__main__":
    unittest.main()
