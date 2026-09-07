"""Unit and round-trip tests for M2.7: AgentRuntime + Real Provider + Tool Round-Trip.

Covers the full M2.7 test matrix:
1. Normal text-only turn (User -> Real Provider -> Final Assistant Text)
2. Single tool round-trip:
   USER: "Sahneyi incele"
   -> Provider: tool_call inspect_scene
   -> inspect_scene ToolDispatcher ile gerçekten çalışır
   -> TOOL RESULT: scene bilgisi
   -> Provider ikinci çağrı
   -> ASSISTANT: "Sahne şu şekilde..."
3. Multiple sequential tool rounds (inspect_scene -> inspect_object -> final response)
4. Provider error handling (HTTP 401/500 -> AgentState.ERROR, AgentErrorEvent)
5. In-flight cancellation (cancel turn, no tool executed, no success event)
6. Max tool-round loop guard (max_tool_rounds exceeded -> controlled AgentError)
7. Stale turn rejection (turn_id mismatch -> dropped, stale_events_count incremented)
8. Tool execution failure (tool returns success=False -> AgentState.ERROR)
9. Synchronous run() execution with real provider
10. Conversation sequence and history integrity validation

Zero Blender (bpy) dependencies. Pure Python standard library only.
"""

import http.server
import json
import socket
import threading
import time
import unittest
from typing import Any, Callable, Dict, List, Optional

from core.config import Config
from core.event_queue import ThreadSafeEventQueue
from core.events import (
    AgentErrorEvent,
    EventType,
    FinalResponseReadyEvent,
    PromptSubmittedEvent,
    ProviderResponseReadyEvent,
    StreamingTextDeltaEvent,
    ToolResultReadyEvent,
)
from core.types import RiskLevel, ToolResult
from agent.dispatcher import ToolDispatcher
from agent.history import HistoryKind
from agent.models import (
    AgentResult,
    ChatMessage,
    Conversation,
    ProviderCompleted,
    ProviderError,
    ProviderErrorType,
    ProviderResponse,
    Role,
    TextDelta,
    ToolCall,
)
from agent.openai_provider import OpenAICompatibleProvider
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from agent.worker import AgentWorker
from tools.base import BaseTool
from tools.registry import ToolRegistry


# -----------------------------------------------------------------------------
# Test Tools & Stub Adapter
# -----------------------------------------------------------------------------

class DummySceneTool(BaseTool):
    name = "inspect_scene"
    description = "Inspect the current 3D scene summary and object counts."
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter, **kwargs) -> ToolResult:
        return ToolResult.ok(
            self.name,
            {"scene_name": "Scene", "counts": {"total": 3, "mesh": 1, "light": 1, "camera": 1}},
        )


class DummyObjectTool(BaseTool):
    name = "inspect_object"
    description = "Inspect details and transforms of a specific named object."
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
        return ToolResult.ok(self.name, {"name": name, "type": "MESH", "location": [0.0, 0.0, 0.0]})


# -----------------------------------------------------------------------------
# Local Fake OpenAI Server
# -----------------------------------------------------------------------------

class FakeOpenAIServerHandler(http.server.BaseHTTPRequestHandler):
    """Configurable local HTTP mock server for OpenAI Chat Completions endpoint."""

    request_history: List[Dict[str, Any]] = []
    custom_responder: Optional[Callable[[Dict[str, Any], "FakeOpenAIServerHandler"], None]] = None
    lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        req_data = json.loads(raw_body.decode("utf-8")) if raw_body else {}

        with self.lock:
            FakeOpenAIServerHandler.request_history.append(req_data)

        # Allow per-test custom responder
        if FakeOpenAIServerHandler.custom_responder is not None:
            FakeOpenAIServerHandler.custom_responder(req_data, self)
            return

        # Default dynamic conversation flow
        messages = req_data.get("messages", [])
        last_msg = messages[-1] if messages else {}
        role = last_msg.get("role")
        content = last_msg.get("content", "")

        if role == "user":
            if "Selam" in content:
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"content":"Merhaba! "}}]}',
                    '{"choices":[{"index":0,"delta":{"content":"Nasıl yardımcı olabilirim?"}}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])
            elif "Sahneyi incele" in content or "Tek tool" in content:
                # LLM decides to call inspect_scene
                tc_chunk = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_scene_99",
                                        "type": "function",
                                        "function": {"name": "inspect_scene", "arguments": "{}"},
                                    }
                                ]
                            },
                        }
                    ]
                }
                finish_chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc_chunk), json.dumps(finish_chunk), "[DONE]"])

            elif "Çoklu inceleme" in content:
                # Round 1: call inspect_scene
                tc_chunk = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_multi_1",
                                        "type": "function",
                                        "function": {"name": "inspect_scene", "arguments": "{}"},
                                    }
                                ]
                            },
                        }
                    ]
                }
                finish_chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc_chunk), json.dumps(finish_chunk), "[DONE]"])

            elif "Sonsuz döngü" in content:
                # Always call inspect_scene
                tc_chunk = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": f"call_loop_{len(FakeOpenAIServerHandler.request_history)}",
                                        "type": "function",
                                        "function": {"name": "inspect_scene", "arguments": "{}"},
                                    }
                                ]
                            },
                        }
                    ]
                }
                finish_chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc_chunk), json.dumps(finish_chunk), "[DONE]"])

            elif "Hatalı tool" in content:
                tc_chunk = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_ghost",
                                        "type": "function",
                                        "function": {
                                            "name": "inspect_object",
                                            "arguments": '{"name": "Ghost"}',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
                finish_chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc_chunk), json.dumps(finish_chunk), "[DONE]"])

            else:
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"content":"Genel cevap."},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])

        elif role == "tool":
            # Tool result arrived at LLM
            tool_name = last_msg.get("name")
            tool_msgs = [m for m in messages if m.get("role") == "tool"]

            # Multi-round flow check
            user_msg = next((m for m in messages if m.get("role") == "user"), {})
            if "Çoklu inceleme" in user_msg.get("content", "") and len(tool_msgs) == 1:
                # Round 2: call inspect_object
                tc_chunk = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_multi_2",
                                        "type": "function",
                                        "function": {
                                            "name": "inspect_object",
                                            "arguments": '{"name": "Cube"}',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
                finish_chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc_chunk), json.dumps(finish_chunk), "[DONE]"])

            elif "Sonsuz döngü" in user_msg.get("content", ""):
                # Keep calling inspect_scene
                tc_chunk = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": f"call_loop_{len(FakeOpenAIServerHandler.request_history)}",
                                        "type": "function",
                                        "function": {"name": "inspect_scene", "arguments": "{}"},
                                    }
                                ]
                            },
                        }
                    ]
                }
                finish_chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc_chunk), json.dumps(finish_chunk), "[DONE]"])

            else:
                # Final synthesis response after tool results
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"content":"Sahne şu şekilde: "}}]}',
                    '{"choices":[{"index":0,"delta":{"content":"3 nesne mevcut (1 mesh, 1 light, 1 camera)."}}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])

    def _send_sse(self, lines: List[str]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for line in lines:
            payload = f"data: {line}\n\n".encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()


# -----------------------------------------------------------------------------
# Test Suite
# -----------------------------------------------------------------------------

class TestAgentRuntimeProviderRoundTrip(unittest.TestCase):
    """Comprehensive test suite for M2.7 round-trip execution."""

    @classmethod
    def setUpClass(cls):
        # Start background fake HTTP server on ephemeral port
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), FakeOpenAIServerHandler)
        cls.port = cls.server.server_port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeOpenAIServerHandler.request_history.clear()
        FakeOpenAIServerHandler.custom_responder = None

        self.registry = ToolRegistry()
        self.registry.register(DummySceneTool())
        self.registry.register(DummyObjectTool())
        self.dispatcher = ToolDispatcher(registry=self.registry, adapter=None)

        self.config = Config(
            base_url=f"http://127.0.0.1:{self.port}/v1",
            model="test-llm-model",
            api_key="test-secret-key",
            timeout_seconds=5.0,
        )
        self.provider = OpenAICompatibleProvider(config=self.config)
        self.event_queue = ThreadSafeEventQueue()
        self.runtime = AgentRuntime(
            provider=self.provider,
            dispatcher=self.dispatcher,
            event_queue=self.event_queue,
            max_tool_rounds=5,
        )

    def tearDown(self):
        self.runtime.shutdown()

    def _drain_and_process(self, max_wait_sec: float = 3.0) -> Optional[AgentResult]:
        """Helper to simulate the main thread event pump until turn completes or times out."""
        deadline = time.time() + max_wait_sec
        last_result = None

        while time.time() < deadline:
            events = self.event_queue.drain_batch(max_items=10, max_time_sec=0.01)
            for ev in events:
                res = self.runtime.process_event(ev)
                if res is not None:
                    last_result = res
            if last_result is not None or self.runtime.current_state in (AgentState.IDLE, AgentState.ERROR):
                if self.runtime.current_turn_id is None and self.runtime.current_state == AgentState.IDLE:
                    break
            time.sleep(0.01)

        return last_result or self.runtime.last_result

    # -------------------------------------------------------------------------
    # 1. Normal Text-Only Turn
    # -------------------------------------------------------------------------
    def test_text_only_turn(self):
        """User prompt -> Provider streams text -> IDLE with final text."""
        turn_id = self.runtime.submit_prompt("Selam")
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

        result = self._drain_and_process()
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "IDLE")
        self.assertEqual(self.runtime.current_state, AgentState.IDLE)
        self.assertIn("Merhaba!", result.final_text)
        self.assertEqual(len(result.tool_results), 0)

        # Verify conversation history
        conv_msgs = self.runtime.conversation.messages
        self.assertEqual(len(conv_msgs), 2)
        self.assertEqual(conv_msgs[0].role, Role.USER)
        self.assertEqual(conv_msgs[0].content, "Selam")
        self.assertEqual(conv_msgs[1].role, Role.ASSISTANT)
        self.assertIn("Merhaba!", conv_msgs[1].content)
        self.runtime.conversation.validate_sequence()

    # -------------------------------------------------------------------------
    # 2. Single Tool Call Round-Trip (Acceptance Scenario)
    # -------------------------------------------------------------------------
    def test_single_tool_call_roundtrip_acceptance(self):
        """USER: 'Sahneyi incele' -> LLM: inspect_scene -> ToolDispatcher executes
        -> TOOL RESULT -> 2nd LLM call -> ASSISTANT: 'Sahne şu şekilde...'
        """
        turn_id = self.runtime.submit_prompt("Sahneyi incele")
        self.assertEqual(self.runtime.current_state, AgentState.PROCESSING)

        result = self._drain_and_process(max_wait_sec=4.0)
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "IDLE")
        self.assertEqual(self.runtime.current_state, AgentState.IDLE)

        # 1. Check final response text
        self.assertIn("Sahne şu şekilde:", result.final_text)
        self.assertIn("3 nesne mevcut", result.final_text)

        # 2. Check tool results
        self.assertEqual(len(result.tool_results), 1)
        self.assertTrue(result.tool_results[0].success)
        self.assertEqual(result.tool_results[0].tool, "inspect_scene")
        self.assertEqual(result.tool_results[0].data["counts"]["total"], 3)

        # 3. Check HTTP server received exactly 2 requests in this turn
        self.assertEqual(len(FakeOpenAIServerHandler.request_history), 2)

        req1 = FakeOpenAIServerHandler.request_history[0]
        req2 = FakeOpenAIServerHandler.request_history[1]

        # First request contained system prompt + user message + tools
        self.assertTrue(any(m["role"] == "user" and "Sahneyi incele" in m["content"] for m in req1["messages"]))
        self.assertTrue(any(t["function"]["name"] == "inspect_scene" for t in req1["tools"]))

        # Second request contained user message + assistant tool_calls + tool result message
        req2_messages = req2["messages"]
        self.assertTrue(any(m["role"] == "tool" and m["tool_call_id"] == "call_scene_99" for m in req2_messages))

        # 4. Check internal Conversation sequence validation
        conv_msgs = self.runtime.conversation.messages
        self.assertEqual(len(conv_msgs), 4)  # User, Assistant(tool_call), Tool, Assistant(final)
        self.assertEqual(conv_msgs[0].role, Role.USER)
        self.assertEqual(conv_msgs[1].role, Role.ASSISTANT)
        self.assertIsNotNone(conv_msgs[1].tool_calls)
        self.assertEqual(conv_msgs[2].role, Role.TOOL)
        self.assertEqual(conv_msgs[2].tool_call_id, "call_scene_99")
        self.assertEqual(conv_msgs[3].role, Role.ASSISTANT)
        self.runtime.conversation.validate_sequence()

        # 5. Check Runtime history
        history_kinds = [item.kind for item in self.runtime.history.items]
        self.assertIn(HistoryKind.USER, history_kinds)
        self.assertIn(HistoryKind.TOOL, history_kinds)
        self.assertIn(HistoryKind.ASSISTANT, history_kinds)

    # -------------------------------------------------------------------------
    # 3. Multiple Sequential Tool Rounds
    # -------------------------------------------------------------------------
    def test_multiple_sequential_tool_rounds(self):
        """Sequential tool rounds: inspect_scene -> inspect_object -> final response."""
        turn_id = self.runtime.submit_prompt("Çoklu inceleme")

        result = self._drain_and_process(max_wait_sec=4.0)
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "IDLE")

        # 2 tools executed across 2 rounds
        self.assertEqual(len(result.tool_results), 2)
        self.assertEqual(result.tool_results[0].tool, "inspect_scene")
        self.assertEqual(result.tool_results[1].tool, "inspect_object")
        self.assertEqual(result.tool_results[1].data["name"], "Cube")

        # HTTP server received exactly 3 requests (Turn 1 -> Tool 1 -> Turn 2 -> Tool 2 -> Final)
        self.assertEqual(len(FakeOpenAIServerHandler.request_history), 3)

        # Conversation contains 6 messages: User, Asst(tc1), Tool(res1), Asst(tc2), Tool(res2), Asst(final)
        conv = self.runtime.conversation
        self.assertEqual(len(conv.messages), 6)
        conv.validate_sequence()

    # -------------------------------------------------------------------------
    # 4. Max Tool Rounds Safety Guard
    # -------------------------------------------------------------------------
    def test_max_tool_rounds_limit_exceeded(self):
        """When LLM enters infinite tool loop, runtime aborts with MAX_TOOL_ROUNDS_EXCEEDED."""
        self.runtime.max_tool_rounds = 2
        turn_id = self.runtime.submit_prompt("Sonsuz döngü")

        result = self._drain_and_process(max_wait_sec=4.0)
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "ERROR")
        self.assertEqual(self.runtime.current_state, AgentState.ERROR)
        self.assertIn("Maximum tool rounds limit reached", result.final_text)

        # History logged the error
        history_items = [h for h in self.runtime.history.items if h.kind == HistoryKind.ERROR]
        self.assertTrue(len(history_items) > 0)
        self.assertIn("MAX_TOOL_ROUNDS_EXCEEDED", history_items[0].title)

    # -------------------------------------------------------------------------
    # 5. Provider Error Handling
    # -------------------------------------------------------------------------
    def test_provider_http_error(self):
        """HTTP error from provider transitions runtime to AgentState.ERROR."""
        def error_responder(req, handler):
            handler.send_response(401)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(b'{"error":{"message":"Invalid API key","type":"invalid_request_error"}}')

        FakeOpenAIServerHandler.custom_responder = error_responder

        turn_id = self.runtime.submit_prompt("Test error")
        result = self._drain_and_process()

        self.assertIsNotNone(result)
        self.assertEqual(result.state, "ERROR")
        self.assertEqual(self.runtime.current_state, AgentState.ERROR)
        self.assertIn("401", result.final_text)

    # -------------------------------------------------------------------------
    # 6. In-Flight Cancellation
    # -------------------------------------------------------------------------
    def test_in_flight_cancellation(self):
        """Cancelling turn aborts execution; no tool is run and no success event emitted."""
        def slow_responder(req, handler):
            time.sleep(0.3)
            tc_chunk = {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "slow_call",
                                    "type": "function",
                                    "function": {"name": "inspect_scene", "arguments": "{}"},
                                }
                            ]
                        },
                    }
                ]
            }
            handler._send_sse([json.dumps(tc_chunk), "[DONE]"])

        FakeOpenAIServerHandler.custom_responder = slow_responder

        turn_id = self.runtime.submit_prompt("Yavaş istek")
        # Immediately cancel
        self.runtime.cancel_current_turn()
        self.assertEqual(self.runtime.current_state, AgentState.IDLE)
        self.assertIsNone(self.runtime.current_turn_id)

        # Drain any residual events
        self._drain_and_process(max_wait_sec=0.5)

        # Verify no tool was executed
        self.assertEqual(len(self.runtime._current_tool_results), 0)
        self.assertEqual(self.runtime.current_state, AgentState.IDLE)

    # -------------------------------------------------------------------------
    # 7. Stale Turn Protection
    # -------------------------------------------------------------------------
    def test_stale_turn_protection(self):
        """Events with mismatched turn_id are discarded without affecting state."""
        self.runtime.submit_prompt("Turn 1")
        initial_stale = self.runtime.stale_events_count

        # Synthetic event with wrong turn ID
        stale_event = ProviderResponseReadyEvent(
            response=ProviderResponse(assistant_text="Stale data", is_final=True),
            turn_id="turn_99999_fake",
        )
        res = self.runtime.process_event(stale_event)
        self.assertIsNone(res)
        self.assertEqual(self.runtime.stale_events_count, initial_stale + 1)

    # -------------------------------------------------------------------------
    # 8. Tool Execution Failure
    # -------------------------------------------------------------------------
    def test_tool_failure_terminates_turn(self):
        """Tool failure halts the turn and transitions runtime to ERROR."""
        turn_id = self.runtime.submit_prompt("Hatalı tool")
        result = self._drain_and_process()

        self.assertIsNotNone(result)
        self.assertEqual(result.state, "ERROR")
        self.assertIn("Ghost", result.final_text)
        self.assertEqual(result.tool_results[0].error.type, "OBJECT_NOT_FOUND")

    # -------------------------------------------------------------------------
    # 9. Synchronous run() execution with real provider
    # -------------------------------------------------------------------------
    def test_synchronous_run_real_provider(self):
        """Synchronous run() method completes tool roundtrip with real provider."""
        result = self.runtime.run("Sahneyi incele")
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "IDLE")
        self.assertIn("Sahne şu şekilde:", result.final_text)
        self.assertEqual(len(result.tool_results), 1)
        self.assertTrue(result.tool_results[0].success)


if __name__ == "__main__":
    unittest.main()
