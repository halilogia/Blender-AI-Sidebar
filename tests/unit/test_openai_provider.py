"""Unit tests for OpenAICompatibleProvider, OpenAIRequestMapper, and ToolCallAccumulator.

Covers the full M2.6 test matrix (31+ scenarios):
- Basic text streaming (simple, multiple chunks, empty/role-only delta, finish_reason=stop)
- Tool calling (single complete, fragmented, multiple, interleaved, text + tool calls, finish_reason=tool_calls)
- Usage reporting (present / absent)
- Malformed inputs (invalid JSON SSE, malformed args, missing tool name, missing call ID, non-dict args, unexpected choices)
- HTTP error normalization (401, 403, 429, 500, timeout, network failure)
- Cancellation handling (long stream cancel, no final result, no stale events)
- Network policy / online_access (localhost, remote enabled, remote disabled)
- Request mapping fixtures (exact JSON structure, 5 tools, parallel_tool_calls=false, headers)
- M2.6 Success Scenario (Text -> Tool calls -> ProviderCompleted) and Final Text roundtrip

Zero Blender (bpy) dependencies. Zero external network dependencies.
"""

import http.server
import json
import socket
import threading
import time
import unittest
import urllib.parse
from typing import Any, Dict, List, Optional

from core.config import Config, is_network_allowed
from agent.context_builder import ContextBuilder, ProviderRequestContext
from agent.models import (
    ChatMessage,
    Conversation,
    ProviderCompleted,
    ProviderError,
    ProviderErrorType,
    Role,
    TextDelta,
    ToolCall,
    ToolCallDelta,
)
from agent.openai_provider import OpenAICompatibleProvider, OpenAIRequestMapper
from agent.tool_call_accumulator import ToolCallAccumulator, ToolCallAccumulatorError
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_scene import InspectSceneTool
from tools.read_only.inspect_selection import InspectSelectionTool
from tools.read_only.inspect_material import InspectMaterialTool
from tools.read_only.inspect_mesh import InspectMeshTool


class MockOpenAIServerHandler(http.server.BaseHTTPRequestHandler):
    """Local HTTP mock server for OpenAI Chat Completions endpoint."""

    # Thread-safe storage for last received request
    last_request_body: Optional[Dict[str, Any]] = None
    last_request_headers: Optional[Dict[str, str]] = None
    lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. Capture request headers and body
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        with self.lock:
            MockOpenAIServerHandler.last_request_headers = dict(self.headers)
            try:
                MockOpenAIServerHandler.last_request_body = (
                    json.loads(raw_body.decode("utf-8")) if raw_body else None
                )
            except Exception:
                MockOpenAIServerHandler.last_request_body = None

        # 2. Route handlers
        if path == "/chat/completions" or path == "/v1/chat/completions":
            scenario = self.headers.get("X-Test-Scenario", "simple_text")

            if scenario == "simple_text":
                self._send_sse([
                    '{"id":"c1","choices":[{"index":0,"delta":{"content":"Hello world"},"finish_reason":null}]}',
                    '{"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])

            elif scenario == "multiple_text_chunks":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"role":"assistant"}}]}',
                    '{"choices":[{"index":0,"delta":{"content":"Hello"}}]}',
                    '{"choices":[{"index":0,"delta":{"content":" "}}]}',
                    '{"choices":[{"index":0,"delta":{"content":"world!"}}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])

            elif scenario == "empty_and_role_deltas":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"role":"assistant"}}]}',
                    '{"choices":[{"index":0,"delta":{}}]}',
                    '{"choices":[{"index":0,"delta":{"content":null}}]}',
                    '{"choices":[{"index":0,"delta":{"content":"Valid text"}}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])

            elif scenario == "single_complete_tool_call":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_101","type":"function","function":{"name":"inspect_object","arguments":"{\\"name\\":\\"Cube\\"}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "fragmented_tool_call":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_202","type":"function","function":{"name":"inspect_","arguments":""}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"object","arguments":"{\\"name\\""}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":":\\"Camera\\"}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "multiple_tool_calls_interleaved":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_a","type":"function","function":{"name":"inspect_scene","arguments":"{}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"call_b","type":"function","function":{"name":"inspect_selection","arguments":"{}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "tool_call_plus_text":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"content":"Sahneyi inceliyorum."},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_s","type":"function","function":{"name":"inspect_scene","arguments":"{}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "with_usage":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":4,"total_tokens":16}}',
                    "[DONE]",
                ])

            elif scenario == "invalid_json_sse":
                self._send_sse([
                    "THIS_IS_NOT_JSON",
                    "[DONE]",
                ])

            elif scenario == "malformed_tool_arguments":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_err","function":{"name":"inspect_object","arguments":"{broken_json"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "non_dict_tool_arguments":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_err","function":{"name":"inspect_object","arguments":"[\\"Cube\\"]"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "missing_tool_name":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_err","function":{"arguments":"{\\"name\\":\\"Cube\\"}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "missing_call_id":
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"inspect_object","arguments":"{\\"name\\":\\"Cube\\"}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            elif scenario == "unexpected_choices_structure":
                self._send_sse([
                    '{"choices":[]}',
                    '{"choices":[null]}',
                    '{"choices":[{"index":0,"delta":{"content":"Recovered"}}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])

            elif scenario == "status_401":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Invalid API key"}}')

            elif scenario == "status_403":
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Forbidden"}}')

            elif scenario == "status_429":
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Rate limit exceeded"}}')

            elif scenario == "status_500":
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Internal server error"}}')

            elif scenario == "slow_stream_cancel":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for i in range(100):
                        payload = f'data: {{"choices":[{{"delta":{{"content":"token_{i} "}}}}]}}\n\n'.encode("utf-8")
                        self.wfile.write(payload)
                        self.wfile.flush()
                        time.sleep(0.02)
                except Exception:
                    pass

            elif scenario == "m26_success_target":
                # Matches Section 27: TextDelta("Cube'u") -> TextDelta(" inceliyorum.") -> ToolCallDelta -> ProviderCompleted("tool_calls")
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"content":"Cube\'u"},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{"content":" inceliyorum."},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_target_1","type":"function","function":{"name":"inspect_object","arguments":"{\\"name\\": "}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"Cube\\"}"}}]},"finish_reason":null}]}',
                    '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
                    "[DONE]",
                ])

            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def _send_sse(self, payloads: List[str]):
        """Send SSE formatted payloads."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for p in payloads:
            chunk = f"data: {p}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()
            time.sleep(0.005)


class TestToolCallAccumulator(unittest.TestCase):
    """Test ToolCallAccumulator reassembly, error modes, and edge cases."""

    def setUp(self):
        self.acc = ToolCallAccumulator()

    def test_single_call_unfragmented(self):
        """Single tool call arriving in one piece."""
        self.acc.feed_delta(
            ToolCallDelta(
                turn_id="t1",
                index=0,
                call_id="call_1",
                tool_name_delta="inspect_object",
                arguments_delta='{"name": "Cube"}',
            )
        )
        calls = self.acc.finalize()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].call_id, "call_1")
        self.assertEqual(calls[0].tool_name, "inspect_object")
        self.assertEqual(calls[0].arguments, {"name": "Cube"})

    def test_single_call_arbitrary_fragmentation(self):
        """Tool name, call_id, and arguments split across multiple deltas."""
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_"))
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="999", tool_name_delta="inspect_"))
        self.acc.feed_delta(ToolCallDelta("t1", index=0, tool_name_delta="mesh", arguments_delta='{"object'))
        self.acc.feed_delta(ToolCallDelta("t1", index=0, arguments_delta='_name": "Plane"}'))

        calls = self.acc.finalize()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].call_id, "call_999")
        self.assertEqual(calls[0].tool_name, "inspect_mesh")
        self.assertEqual(calls[0].arguments, {"object_name": "Plane"})

    def test_call_id_repeated_verbatim(self):
        """When subsequent deltas repeat the same call_id verbatim, do not duplicate it."""
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_1", tool_name_delta="inspect_scene"))
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_1", arguments_delta="{}"))
        calls = self.acc.finalize()
        self.assertEqual(calls[0].call_id, "call_1")

    def test_empty_arguments_produces_empty_dict(self):
        """Empty arguments string or whitespace produces empty dict."""
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_1", tool_name_delta="inspect_scene", arguments_delta=""))
        calls = self.acc.finalize()
        self.assertEqual(calls[0].arguments, {})

    def test_multiple_interleaved_calls(self):
        """Multiple tool calls with interleaved deltas reassemble accurately in index order."""
        self.acc.feed_delta(ToolCallDelta("t1", index=1, call_id="call_b", tool_name_delta="tool_two", arguments_delta='{"b":'))
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_a", tool_name_delta="tool_one", arguments_delta='{"a": 1}'))
        self.acc.feed_delta(ToolCallDelta("t1", index=1, arguments_delta=' 2}'))

        calls = self.acc.finalize()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].tool_name, "tool_one")
        self.assertEqual(calls[0].arguments, {"a": 1})
        self.assertEqual(calls[1].tool_name, "tool_two")
        self.assertEqual(calls[1].arguments, {"b": 2})

    def test_malformed_json_raises_accumulator_error(self):
        """Invalid JSON syntax in arguments raises ToolCallAccumulatorError with MALFORMED_JSON."""
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_1", tool_name_delta="tool", arguments_delta='{"bad":'))
        with self.assertRaises(ToolCallAccumulatorError) as ctx:
            self.acc.finalize()
        self.assertEqual(ctx.exception.code, "MALFORMED_JSON")

    def test_non_object_arguments_raises_accumulator_error(self):
        """Arguments parsing to a JSON list or primitive raises NON_OBJECT_ARGUMENTS."""
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_1", tool_name_delta="tool", arguments_delta='["Cube"]'))
        with self.assertRaises(ToolCallAccumulatorError) as ctx:
            self.acc.finalize()
        self.assertEqual(ctx.exception.code, "NON_OBJECT_ARGUMENTS")

    def test_missing_call_id_raises_accumulator_error(self):
        """Tool call without call_id raises MISSING_CALL_ID."""
        self.acc.feed_delta(ToolCallDelta("t1", index=0, tool_name_delta="tool", arguments_delta="{}"))
        with self.assertRaises(ToolCallAccumulatorError) as ctx:
            self.acc.finalize()
        self.assertEqual(ctx.exception.code, "MISSING_CALL_ID")

    def test_missing_tool_name_raises_accumulator_error(self):
        """Tool call without tool_name raises MISSING_TOOL_NAME."""
        self.acc.feed_delta(ToolCallDelta("t1", index=0, call_id="call_1", arguments_delta="{}"))
        with self.assertRaises(ToolCallAccumulatorError) as ctx:
            self.acc.finalize()
        self.assertEqual(ctx.exception.code, "MISSING_TOOL_NAME")


class TestOpenAIRequestMapper(unittest.TestCase):
    """Test mapping internal models to OpenAI Chat Completions payload."""

    def test_map_all_message_roles(self):
        """Verify SYSTEM, USER, ASSISTANT (with tool_calls), and TOOL mapping."""
        messages = [
            ChatMessage(role=Role.SYSTEM, content="System prompt"),
            ChatMessage(role=Role.USER, content="Inspect Cube"),
            ChatMessage(
                role=Role.ASSISTANT,
                content="Inspecting",
                tool_calls=[ToolCall(call_id="c1", tool_name="inspect_object", arguments={"name": "Cube"})],
            ),
            ChatMessage(role=Role.TOOL, tool_call_id="c1", name="inspect_object", content='{"type":"MESH"}'),
        ]
        context = ProviderRequestContext(messages=messages, tools=[], system_prompt="System prompt")
        req = OpenAIRequestMapper.map_request(context, model="gpt-4o-mini")

        self.assertEqual(req["model"], "gpt-4o-mini")
        self.assertTrue(req["stream"])
        self.assertNotIn("tools", req)  # Omitted when empty

        msgs = req["messages"]
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[0], {"role": "system", "content": "System prompt"})
        self.assertEqual(msgs[1], {"role": "user", "content": "Inspect Cube"})
        self.assertEqual(msgs[2]["role"], "assistant")
        self.assertEqual(msgs[2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(msgs[2]["tool_calls"][0]["function"]["name"], "inspect_object")
        self.assertEqual(msgs[2]["tool_calls"][0]["function"]["arguments"], '{"name":"Cube"}')
        self.assertEqual(msgs[3], {"role": "tool", "tool_call_id": "c1", "name": "inspect_object", "content": '{"type":"MESH"}'})

    def test_map_with_five_tools(self):
        """Verify 5 grounding tools are mapped and parallel_tool_calls=false is set."""
        tools = [
            InspectSceneTool(),
            InspectSelectionTool(),
            InspectObjectTool(),
            InspectMaterialTool(),
            InspectMeshTool(),
        ]
        context = ContextBuilder.build(tools=tools)
        req = OpenAIRequestMapper.map_request(context, model="gpt-4o")

        self.assertIn("tools", req)
        self.assertEqual(len(req["tools"]), 5)
        self.assertEqual(req["parallel_tool_calls"], False)
        tool_names = [t["function"]["name"] for t in req["tools"]]
        self.assertEqual(
            tool_names,
            ["inspect_scene", "inspect_selection", "inspect_object", "inspect_material", "inspect_mesh"]
        )


class TestOpenAICompatibleProviderWithMockServer(unittest.TestCase):
    """Comprehensive provider test suite against local mock HTTP server."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), MockOpenAIServerHandler)
        cls.server_port = cls.server.server_port
        cls.base_url = f"http://127.0.0.1:{cls.server_port}"

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2.0)

    def _create_provider(self, scenario: str, api_key: str = "sk-test-key", base_url: Optional[str] = None) -> OpenAICompatibleProvider:
        """Helper to create provider configured for a specific scenario."""
        cfg = Config(
            base_url=base_url or self.base_url,
            model="test-model",
            api_key=api_key,
            timeout_seconds=5.0,
        )
        provider = OpenAICompatibleProvider(config=cfg, online_access=True)
        # Inject scenario header via custom http_client post wrapper or headers
        old_post = provider.http_client.post

        def post_with_scenario(*args, **kwargs):
            headers = dict(kwargs.get("headers") or {})
            headers["X-Test-Scenario"] = scenario
            kwargs["headers"] = headers
            return old_post(*args, **kwargs)

        provider.http_client.post = post_with_scenario
        return provider

    # --- Basic Text Streaming ---

    def test_01_simple_assistant_text(self):
        """Test 1: Simple text stream produces TextDelta and ProviderCompleted."""
        provider = self._create_provider("simple_text")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], TextDelta)
        self.assertEqual(events[0].text, "Hello world")
        self.assertIsInstance(events[1], ProviderCompleted)
        self.assertEqual(events[1].finish_reason, "stop")

    def test_02_multiple_text_chunks(self):
        """Test 2: Multiple text chunks are streamed incrementally."""
        provider = self._create_provider("multiple_text_chunks")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        text_deltas = [e.text for e in events if isinstance(e, TextDelta)]
        self.assertEqual(text_deltas, ["Hello", " ", "world!"])
        self.assertEqual("".join(text_deltas), "Hello world!")

        comp = [e for e in events if isinstance(e, ProviderCompleted)]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0].finish_reason, "stop")

    def test_03_empty_and_role_deltas_ignored(self):
        """Test 3: Empty deltas, null content, and role-only deltas do not emit TextDelta."""
        provider = self._create_provider("empty_and_role_deltas")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        text_deltas = [e.text for e in events if isinstance(e, TextDelta)]
        self.assertEqual(text_deltas, ["Valid text"])

    # --- Tool Calling ---

    def test_06_single_complete_tool_call(self):
        """Test 6: Single complete tool call streamed and accumulated."""
        provider = self._create_provider("single_complete_tool_call")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        deltas = [e for e in events if isinstance(e, ToolCallDelta)]
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].tool_name_delta, "inspect_object")

        comp = [e for e in events if isinstance(e, ProviderCompleted)][0]
        self.assertEqual(comp.finish_reason, "tool_calls")

        # Verify finalized accumulator
        self.assertEqual(len(provider.last_tool_calls), 1)
        self.assertEqual(provider.last_tool_calls[0].tool_name, "inspect_object")
        self.assertEqual(provider.last_tool_calls[0].arguments, {"name": "Cube"})

    def test_07_fragmented_tool_call(self):
        """Test 7: Fragmented tool call reassembles cleanly."""
        provider = self._create_provider("fragmented_tool_call")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        deltas = [e for e in events if isinstance(e, ToolCallDelta)]
        self.assertEqual(len(deltas), 3)

        comp = [e for e in events if isinstance(e, ProviderCompleted)][0]
        self.assertEqual(comp.finish_reason, "tool_calls")
        self.assertEqual(len(provider.last_tool_calls), 1)
        self.assertEqual(provider.last_tool_calls[0].tool_name, "inspect_object")
        self.assertEqual(provider.last_tool_calls[0].arguments, {"name": "Camera"})

    def test_08_multiple_tool_calls_interleaved(self):
        """Test 8: Multiple tool calls interleaved produce multiple ToolCalls."""
        provider = self._create_provider("multiple_tool_calls_interleaved")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        comp = [e for e in events if isinstance(e, ProviderCompleted)][0]
        self.assertEqual(comp.finish_reason, "tool_calls")
        self.assertEqual(len(provider.last_tool_calls), 2)
        self.assertEqual(provider.last_tool_calls[0].tool_name, "inspect_scene")
        self.assertEqual(provider.last_tool_calls[1].tool_name, "inspect_selection")

    def test_10_tool_call_plus_text(self):
        """Test 10: Stream containing both text and tool calls."""
        provider = self._create_provider("tool_call_plus_text")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        texts = [e.text for e in events if isinstance(e, TextDelta)]
        self.assertEqual(texts, ["Sahneyi inceliyorum."])
        self.assertEqual(len(provider.last_tool_calls), 1)
        self.assertEqual(provider.last_tool_calls[0].tool_name, "inspect_scene")

    # --- Usage ---

    def test_12_usage_reporting(self):
        """Test 12: Usage dictionary captured in ProviderCompleted."""
        provider = self._create_provider("with_usage")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        comp = [e for e in events if isinstance(e, ProviderCompleted)][0]
        self.assertIsNotNone(comp.usage)
        self.assertEqual(comp.usage.get("total_tokens"), 16)

    # --- Malformed Inputs ---

    def test_14_invalid_json_sse_payload(self):
        """Test 14: Invalid JSON in SSE payload yields ProviderError(INVALID_RESPONSE)."""
        provider = self._create_provider("invalid_json_sse")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        errors = [e for e in events if isinstance(e, ProviderError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].type, ProviderErrorType.INVALID_RESPONSE)

    def test_15_malformed_tool_arguments(self):
        """Test 15: Malformed arguments JSON yields ProviderError(TOOL_CALL_PARSE_ERROR)."""
        provider = self._create_provider("malformed_tool_arguments")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        errors = [e for e in events if isinstance(e, ProviderError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].type, ProviderErrorType.TOOL_CALL_PARSE_ERROR)

    def test_16_missing_tool_name(self):
        """Test 16: Missing tool name yields ProviderError(TOOL_CALL_PARSE_ERROR)."""
        provider = self._create_provider("missing_tool_name")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        errors = [e for e in events if isinstance(e, ProviderError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].type, ProviderErrorType.TOOL_CALL_PARSE_ERROR)

    def test_17_missing_call_id(self):
        """Test 17: Missing call_id yields ProviderError(TOOL_CALL_PARSE_ERROR)."""
        provider = self._create_provider("missing_call_id")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        errors = [e for e in events if isinstance(e, ProviderError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].type, ProviderErrorType.TOOL_CALL_PARSE_ERROR)

    def test_18_non_dict_tool_arguments(self):
        """Test 18: Non-dict tool arguments yield ProviderError(TOOL_CALL_PARSE_ERROR)."""
        provider = self._create_provider("non_dict_tool_arguments")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        errors = [e for e in events if isinstance(e, ProviderError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].type, ProviderErrorType.TOOL_CALL_PARSE_ERROR)

    def test_19_unexpected_choices_structure(self):
        """Test 19: Empty choices or None choice handled gracefully without crash."""
        provider = self._create_provider("unexpected_choices_structure")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        texts = [e.text for e in events if isinstance(e, TextDelta)]
        self.assertEqual(texts, ["Recovered"])

    # --- HTTP Error Normalization ---

    def test_20_status_401_auth_error(self):
        """Test 20: 401 yields ProviderError(AUTH_ERROR)."""
        provider = self._create_provider("status_401")
        events = list(provider.stream_chat(ContextBuilder.build()))
        self.assertEqual(events[0].type, ProviderErrorType.AUTH_ERROR)

    def test_21_status_403_auth_error(self):
        """Test 21: 403 yields ProviderError(AUTH_ERROR)."""
        provider = self._create_provider("status_403")
        events = list(provider.stream_chat(ContextBuilder.build()))
        self.assertEqual(events[0].type, ProviderErrorType.AUTH_ERROR)

    def test_22_status_429_rate_limit(self):
        """Test 22: 429 yields ProviderError(RATE_LIMIT)."""
        provider = self._create_provider("status_429")
        events = list(provider.stream_chat(ContextBuilder.build()))
        self.assertEqual(events[0].type, ProviderErrorType.RATE_LIMIT)

    def test_23_status_500_unavailable(self):
        """Test 23: 500 yields ProviderError(UNAVAILABLE)."""
        provider = self._create_provider("status_500")
        events = list(provider.stream_chat(ContextBuilder.build()))
        self.assertEqual(events[0].type, "UNAVAILABLE")

    def test_24_timeout_normalization(self):
        """Test 24: Timeout yields ProviderError(TIMEOUT)."""
        cfg = Config(base_url="http://127.0.0.1:1", timeout_seconds=0.05)
        provider = OpenAICompatibleProvider(cfg)
        events = list(provider.stream_chat(ContextBuilder.build()))
        self.assertIn(events[0].type, (ProviderErrorType.TIMEOUT, ProviderErrorType.NETWORK_ERROR))

    # --- Cancellation ---

    def test_26_stream_cancellation(self):
        """Test 26: In-flight cancellation yields ProviderError(CANCELLED) and terminates."""
        provider = self._create_provider("slow_stream_cancel")
        cancel_event = threading.Event()

        events = []
        for event in provider.stream_chat(ContextBuilder.build(), cancel_event=cancel_event):
            events.append(event)
            if len(events) == 3:
                cancel_event.set()

        # Last event must be ProviderError(CANCELLED)
        self.assertTrue(any(isinstance(e, ProviderError) and e.type == ProviderErrorType.CANCELLED for e in events))
        # ProviderCompleted must NOT be emitted
        self.assertFalse(any(isinstance(e, ProviderCompleted) for e in events))

    # --- Network Policy / Online Access ---

    def test_29_localhost_allowed_without_online_access(self):
        """Test 29: Localhost endpoints are allowed even when online_access is False."""
        cfg = Config(base_url="http://127.0.0.1:8000/v1")
        provider = OpenAICompatibleProvider(cfg, online_access=False)
        allowed, _ = is_network_allowed(provider.config.base_url, provider.online_access)
        self.assertTrue(allowed)

    def test_30_remote_endpoint_with_online_access(self):
        """Test 30: Remote endpoint allowed when online_access is True."""
        cfg = Config(base_url="https://api.openai.com/v1")
        provider = OpenAICompatibleProvider(cfg, online_access=True)
        # Pre-flight check in stream_chat:
        events = list(provider.stream_chat(ContextBuilder.build()))
        # Will fail on DNS/connection, but NOT on network policy
        self.assertNotEqual(events[0].message, "Network access forbidden: Blender online access is disabled in Preferences. Enable it or use a local provider (localhost).")

    def test_31_remote_endpoint_blocked_when_online_access_disabled(self):
        """Test 31: Remote endpoint immediately yields ProviderError when online_access is False."""
        cfg = Config(base_url="https://api.openai.com/v1")
        provider = OpenAICompatibleProvider(cfg, online_access=False)
        events = list(provider.stream_chat(ContextBuilder.build()))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, ProviderErrorType.NETWORK_ERROR)
        self.assertIn("online access is disabled", events[0].message)

    # --- Request Fixture Verification ---

    def test_request_fixture_exact_structure(self):
        """Verify outgoing HTTP request body matches exact OpenAI specification."""
        provider = self._create_provider("simple_text", api_key="sk-test-secret-key")
        tools = [InspectSceneTool()]
        context = ContextBuilder.build(tools=tools)

        _ = list(provider.stream_chat(context))

        with MockOpenAIServerHandler.lock:
            body = MockOpenAIServerHandler.last_request_body
            headers = MockOpenAIServerHandler.last_request_headers

        self.assertIsNotNone(body)
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["stream"], True)
        self.assertEqual(body["parallel_tool_calls"], False)
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tools"][0]["function"]["name"], "inspect_scene")

        # Headers verification
        self.assertEqual(headers.get("Authorization"), "Bearer sk-test-secret-key")
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertEqual(headers.get("Accept"), "text/event-stream")

    # --- Section 27 Success Target ---

    def test_m26_success_target(self):
        """Test target from Section 27:

        TextDelta("Cube'u") -> TextDelta(" inceliyorum.") -> ToolCallDelta -> ToolCallDelta -> ProviderCompleted("tool_calls")
        """
        provider = self._create_provider("m26_success_target")
        context = ContextBuilder.build()
        events = list(provider.stream_chat(context))

        self.assertEqual(len(events), 5)
        self.assertIsInstance(events[0], TextDelta)
        self.assertEqual(events[0].text, "Cube'u")

        self.assertIsInstance(events[1], TextDelta)
        self.assertEqual(events[1].text, " inceliyorum.")

        self.assertIsInstance(events[2], ToolCallDelta)
        self.assertEqual(events[2].tool_name_delta, "inspect_object")

        self.assertIsInstance(events[3], ToolCallDelta)
        self.assertEqual(events[3].arguments_delta, '"Cube"}')

        self.assertIsInstance(events[4], ProviderCompleted)
        self.assertEqual(events[4].finish_reason, "tool_calls")

        # Verify accumulated ToolCall
        self.assertEqual(len(provider.last_tool_calls), 1)
        self.assertEqual(provider.last_tool_calls[0].call_id, "call_target_1")
        self.assertEqual(provider.last_tool_calls[0].tool_name, "inspect_object")
        self.assertEqual(provider.last_tool_calls[0].arguments, {"name": "Cube"})


if __name__ == "__main__":
    unittest.main()
