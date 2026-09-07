"""Pure Python unit tests for Phase 6 Async Boundary, Events, Worker, and Queue.

Zero Blender dependencies.
"""

import time
import threading
import unittest
from typing import Any, Dict, List, Optional

from core.events import (
    AgentErrorEvent,
    CancelRequestedEvent,
    Event,
    EventType,
    FinalResponseReadyEvent,
    PromptSubmittedEvent,
    ProviderResponseReadyEvent,
    ShutdownEvent,
    ToolCallRequestedEvent,
    ToolResultReadyEvent,
    TurnMetrics,
)
from core.event_queue import ThreadSafeEventQueue
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool
from tools.registry import ToolRegistry
from agent.dispatcher import ToolDispatcher
from agent.mock_provider import MockProvider
from agent.models import ProviderResponse, ToolCall
from agent.provider import BaseProvider
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from agent.worker import AgentWorker


class MockDummyTool(BaseTool):
    """Dummy tool for dispatcher testing."""

    name = "dummy_tool"
    description = "A dummy test tool."
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {
            "val": {"type": "string"},
        },
        "required": ["val"],
    }

    def execute(self, adapter: Any, **kwargs: Any) -> ToolResult:
        return ToolResult.ok(self.name, {"echo": kwargs.get("val")})


class ErrorThrowingProvider(BaseProvider):
    """Provider that raises an unhandled exception to test worker isolation."""

    def generate(self, prompt: str, tool_results: Optional[List[ToolResult]] = None) -> ProviderResponse:
        raise RuntimeError("Simulated network outage or parse crash")


class TestEventsAndMetrics(unittest.TestCase):
    """Test suite for event dataclasses and turn metrics."""

    def test_turn_metrics_serialization(self):
        t0 = time.time()
        metrics = TurnMetrics(turn_id="turn_1", t_submitted=t0)
        self.assertEqual(metrics.turn_id, "turn_1")
        self.assertIsNone(metrics.total_duration_sec)

        metrics.t_first_event = t0 + 0.05
        metrics.t_tools_duration = 0.012
        metrics.t_completed = t0 + 0.12

        d = metrics.to_dict()
        self.assertEqual(d["turn_id"], "turn_1")
        self.assertAlmostEqual(d["total_duration_sec"], 0.12, places=2)
        self.assertAlmostEqual(d["t_tools_duration"], 0.012, places=3)

    def test_event_hierarchy_and_dict(self):
        ev = PromptSubmittedEvent(prompt="Test prompt", turn_id="turn_42")
        d = ev.to_dict()
        self.assertEqual(d["event_type"], EventType.PROMPT_SUBMITTED)
        self.assertEqual(d["turn_id"], "turn_42")
        self.assertEqual(d["payload"]["prompt"], "Test prompt")

    def test_agent_error_event(self):
        ev = AgentErrorEvent(
            error_type="TIMEOUT",
            message="Connection timed out",
            turn_id="turn_5",
            details={"code": 408},
        )
        d = ev.to_dict()
        self.assertEqual(d["event_type"], EventType.AGENT_ERROR)
        self.assertEqual(d["payload"]["error_type"], "TIMEOUT")
        self.assertEqual(d["payload"]["message"], "Connection timed out")
        self.assertEqual(d["payload"]["details"]["code"], 408)


class TestThreadSafeEventQueue(unittest.TestCase):
    """Test suite for ThreadSafeEventQueue and starvation prevention."""

    def test_enqueue_dequeue(self):
        queue = ThreadSafeEventQueue()
        self.assertTrue(queue.is_empty())

        ev1 = PromptSubmittedEvent(prompt="P1", turn_id="turn_1")
        queue.put(ev1)
        self.assertFalse(queue.is_empty())
        self.assertEqual(queue.qsize(), 1)

        popped = queue.get_nowait()
        self.assertEqual(popped, ev1)
        self.assertTrue(queue.is_empty())
        self.assertIsNone(queue.get_nowait())

    def test_bounded_batch_draining_by_count(self):
        queue = ThreadSafeEventQueue()
        for i in range(25):
            queue.put(PromptSubmittedEvent(prompt=f"P_{i}", turn_id="turn_1"))

        # Drain batch with max 10 items
        batch = queue.drain_batch(max_items=10, max_time_sec=1.0)
        self.assertEqual(len(batch), 10)
        self.assertEqual(queue.qsize(), 15)

    def test_clear_queue(self):
        queue = ThreadSafeEventQueue()
        for i in range(5):
            queue.put(PromptSubmittedEvent(prompt=f"P_{i}", turn_id="turn_1"))
        discarded = queue.clear()
        self.assertEqual(discarded, 5)
        self.assertTrue(queue.is_empty())


class TestAgentWorker(unittest.TestCase):
    """Test suite for background worker thread execution and isolation."""

    def setUp(self):
        self.provider = MockProvider()
        self.queue = ThreadSafeEventQueue()
        self.worker = AgentWorker(provider=self.provider, event_queue=self.queue)

    def tearDown(self):
        self.worker.stop(timeout=0.5)

    def test_worker_lifecycle(self):
        self.assertFalse(self.worker.is_running)
        self.worker.start()
        self.assertTrue(self.worker.is_running)
        self.worker.stop()
        self.assertFalse(self.worker.is_running)

    def test_worker_executes_task_and_puts_event(self):
        self.worker.start()
        cancel_ev = threading.Event()
        self.worker.submit_task(
            turn_id="turn_test_1",
            prompt="Mevcut sahneyi incele",
            cancel_event=cancel_ev,
        )

        # Wait for worker to produce event
        event = None
        for _ in range(50):
            event = self.queue.get_nowait()
            if event:
                break
            time.sleep(0.01)

        self.assertIsNotNone(event)
        self.assertIsInstance(event, ProviderResponseReadyEvent)
        self.assertEqual(event.turn_id, "turn_test_1")
        self.assertEqual(len(event.response.tool_calls), 1)
        self.assertEqual(event.response.tool_calls[0].tool_name, "inspect_scene")

    def test_worker_respects_cancellation(self):
        self.worker.start()
        cancel_ev = threading.Event()
        cancel_ev.set()  # Cancelled before execution

        self.worker.submit_task(
            turn_id="turn_cancelled",
            prompt="Mevcut sahneyi incele",
            cancel_event=cancel_ev,
        )

        time.sleep(0.05)
        self.assertTrue(self.queue.is_empty(), "Worker should not produce event for cancelled task")

    def test_worker_isolates_exceptions(self):
        error_provider = ErrorThrowingProvider()
        worker = AgentWorker(provider=error_provider, event_queue=self.queue)
        worker.start()

        worker.submit_task(
            turn_id="turn_err",
            prompt="Crash me",
        )

        event = None
        for _ in range(50):
            event = self.queue.get_nowait()
            if event:
                break
            time.sleep(0.01)

        worker.stop()
        self.assertIsNotNone(event)
        self.assertIsInstance(event, AgentErrorEvent)
        self.assertEqual(event.error_type, "WORKER_EXCEPTION")
        self.assertIn("Simulated network outage", event.message)


class TestAsyncAgentRuntime(unittest.TestCase):
    """Test suite for AgentRuntime async turn execution, dispatch, and stale filtering."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(MockDummyTool())
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=None)
        self.provider = MockProvider()
        self.event_queue = ThreadSafeEventQueue()
        self.worker = AgentWorker(provider=self.provider, event_queue=self.event_queue)
        self.runtime = AgentRuntime(
            provider=self.provider,
            dispatcher=self.dispatcher,
            event_queue=self.event_queue,
            worker=self.worker,
        )

    def tearDown(self):
        self.runtime.shutdown()

    def test_submit_prompt_initializes_turn(self):
        turn_id = self.runtime.submit_prompt("Test prompt")
        self.assertEqual(turn_id, "turn_1")
        self.assertEqual(self.runtime.current_turn_id, "turn_1")
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

    def test_stale_event_rejected(self):
        self.runtime.submit_prompt("Turn 1 prompt")
        self.assertEqual(self.runtime.current_turn_id, "turn_1")

        # Create an event with an old turn ID
        stale_event = ProviderResponseReadyEvent(
            response=ProviderResponse(assistant_text="Old response", is_final=True),
            turn_id="turn_old_99",
        )

        result = self.runtime.process_event(stale_event)
        self.assertIsNone(result)
        self.assertEqual(self.runtime.stale_events_count, 1)
        self.assertEqual(self.runtime.current_turn_id, "turn_1")
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

    def test_cancellation_clears_turn_and_resets_state(self):
        self.runtime.submit_prompt("Turn to cancel")
        self.assertEqual(self.runtime.current_turn_id, "turn_1")
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

        self.runtime.cancel_current_turn()
        self.assertIsNone(self.runtime.current_turn_id)
        self.assertEqual(self.runtime.current_state, AgentState.IDLE)

        # Pending event for turn_1 now gets rejected as stale
        stale_ev = ProviderResponseReadyEvent(
            response=ProviderResponse(assistant_text="Late arriving", is_final=True),
            turn_id="turn_1",
        )
        self.runtime.process_event(stale_ev)
        self.assertEqual(self.runtime.stale_events_count, 1)

    def test_full_async_cycle_with_direct_events(self):
        # 1. Submit prompt
        turn_id = self.runtime.submit_prompt("Cube'u incele")
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

        # 2. Worker generates response with tool call -> event
        resp = ProviderResponse(
            assistant_text=None,
            tool_calls=[ToolCall(call_id="call_dummy", tool_name="dummy_tool", arguments={"val": "Cube"})],
            is_final=False,
        )
        ev1 = ProviderResponseReadyEvent(response=resp, turn_id=turn_id)
        res1 = self.runtime.process_event(ev1)
        self.assertIsNone(res1)  # Tool executed, waiting for next generation step
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

        # 3. Final synthesis event arrives
        final_resp = ProviderResponse(
            assistant_text="Cube dummy tool completed: Cube",
            tool_calls=[],
            is_final=True,
        )
        ev2 = ProviderResponseReadyEvent(response=final_resp, turn_id=turn_id)
        final_res = self.runtime.process_event(ev2)

        self.assertIsNotNone(final_res)
        self.assertEqual(self.runtime.current_state, AgentState.IDLE)
        self.assertEqual(final_res.state, "IDLE")
        self.assertEqual(len(final_res.tool_results), 1)
        self.assertEqual(final_res.tool_results[0].data["echo"], "Cube")
        self.assertIn("Cube dummy tool completed", final_res.final_text)


if __name__ == "__main__":
    unittest.main()
