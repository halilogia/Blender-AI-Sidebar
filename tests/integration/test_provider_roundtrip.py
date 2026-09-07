"""Headless integration tests for M2.7: AgentRuntime + Real Provider + Tool Round-Trip in Blender 5.2.1 LTS.

Verifies the entire execution chain inside live headless Blender:
USER
  -> AgentRuntime
  -> OpenAICompatibleProvider
  -> ProviderStreamEvent
  -> ToolDispatcher (executing grounding tools on Blender main thread with real bpy data)
  -> ToolResult
  -> Conversation
  -> 2nd Provider Call (with updated context including real tool result)
  -> FINAL Assistant Response

Zero external network dependencies. Runs against local ephemeral HTTP fake server.
"""

import http.server
import json
import os
import sys
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy
import importlib.util

init_path = os.path.join(PROJECT_ROOT, "__init__.py")
spec = importlib.util.spec_from_file_location(
    "blender_ai_sidebar",
    init_path,
    submodule_search_locations=[PROJECT_ROOT],
)
blender_ai_sidebar = importlib.util.module_from_spec(spec)
sys.modules["blender_ai_sidebar"] = blender_ai_sidebar
spec.loader.exec_module(blender_ai_sidebar)

from adapter.blender_adapter import BlenderAdapter
from agent.dispatcher import ToolDispatcher
from agent.models import Role
from agent.openai_provider import OpenAICompatibleProvider
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from core.config import Config
from core.event_queue import ThreadSafeEventQueue
from tools.read_only.inspect_material import InspectMaterialTool
from tools.read_only.inspect_mesh import InspectMeshTool
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_scene import InspectSceneTool
from tools.read_only.inspect_selection import InspectSelectionTool
from tools.registry import ToolRegistry
from ui.timer_bridge import TimerBridge


# -----------------------------------------------------------------------------
# Local Fake OpenAI Server for Blender Headless Testing
# -----------------------------------------------------------------------------

class BlenderHeadlessFakeServerHandler(http.server.BaseHTTPRequestHandler):
    """Responds with OpenAI-compatible SSE events for grounding tool round-trips."""

    request_history = []
    lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        req = json.loads(raw.decode("utf-8")) if raw else {}

        with self.lock:
            BlenderHeadlessFakeServerHandler.request_history.append(req)

        messages = req.get("messages", [])
        last_msg = messages[-1] if messages else {}
        role = last_msg.get("role")
        content = last_msg.get("content", "")

        if role == "user":
            if "Sahneyi incele" in content:
                # Decide to call inspect_scene
                tc = {
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_b3d_scene_01",
                                "type": "function",
                                "function": {"name": "inspect_scene", "arguments": "{}"},
                            }]
                        },
                    }]
                }
                fn = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc), json.dumps(fn), "[DONE]"])

            elif "Cube objesini incele" in content:
                # Decide to call inspect_object for Cube
                tc = {
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_b3d_obj_01",
                                "type": "function",
                                "function": {"name": "inspect_object", "arguments": '{"name": "Cube"}'},
                            }]
                        },
                    }]
                }
                fn = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc), json.dumps(fn), "[DONE]"])

            elif "Çoklu inceleme" in content:
                # Round 1: call inspect_scene
                tc = {
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_b3d_multi_1",
                                "type": "function",
                                "function": {"name": "inspect_scene", "arguments": "{}"},
                            }]
                        },
                    }]
                }
                fn = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc), json.dumps(fn), "[DONE]"])

            else:
                self._send_sse([
                    '{"choices":[{"index":0,"delta":{"content":"Blender AI Sidebar haz\u0131r."},"finish_reason":"stop"}]}',
                    "[DONE]",
                ])

        elif role == "tool":
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            user_msg = next((m for m in reversed(messages) if m.get("role") == "user"), {})

            if "Çoklu inceleme" in user_msg.get("content", "") and len(tool_msgs) == 1:
                # Round 2: call inspect_object for Cube
                tc = {
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_b3d_multi_2",
                                "type": "function",
                                "function": {"name": "inspect_object", "arguments": '{"name": "Cube"}'},
                            }]
                        },
                    }]
                }
                fn = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                self._send_sse([json.dumps(tc), json.dumps(fn), "[DONE]"])

            else:
                # Final synthesis with data received from the tool
                # Parse tool content to show real data in synthesis
                try:
                    tool_data = json.loads(last_msg.get("content", "{}"))
                except Exception:
                    tool_data = {}

                if "counts" in tool_data:
                    total = tool_data["counts"].get("total", 0)
                    resp_text = f"Sahne ba\u015far\u0131yla incelendi. Toplam {total} nesne mevcut."
                elif "type" in tool_data:
                    name = tool_data.get("name", "Bilinmeyen")
                    otype = tool_data.get("type", "")
                    resp_text = f"Nesne '{name}' ba\u015far\u0131yla incelendi (Tip: {otype})."
                else:
                    resp_text = "\u0130nceleme tamamland\u0131."

                self._send_sse([
                    f'{{"choices":[{{"index":0,"delta":{{"content":"{resp_text}"}},"finish_reason":"stop"}}]}}',
                    "[DONE]",
                ])

    def _send_sse(self, lines):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for l in lines:
            self.wfile.write(f"data: {l}\n\n".encode("utf-8"))
            self.wfile.flush()


def pump_timer_until_idle(bridge: TimerBridge, runtime: AgentRuntime, timeout: float = 3.0) -> bool:
    """Pump timer ticks on the Blender main thread until the agent reaches IDLE or times out."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        bridge.tick()
        if runtime.current_state in (AgentState.IDLE, AgentState.ERROR) and runtime.current_turn_id is None:
            return True
        time.sleep(0.01)
    return False


def run_tests():
    print("\n=== STARTING M2.7 REAL PROVIDER + TOOL ROUND-TRIP HEADLESS INTEGRATION TEST ===")

    # 1. Start local ephemeral mock HTTP server
    server = http.server.HTTPServer(("127.0.0.1", 0), BlenderHeadlessFakeServerHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        # 2. Setup real grounding tools and BlenderAdapter
        registry = ToolRegistry()
        registry.register(InspectSceneTool())
        registry.register(InspectSelectionTool())
        registry.register(InspectObjectTool())
        registry.register(InspectMaterialTool())
        registry.register(InspectMeshTool())

        adapter = BlenderAdapter()
        dispatcher = ToolDispatcher(registry=registry, adapter=adapter)

        # 3. Setup real OpenAICompatibleProvider pointing to local server
        config = Config(
            base_url=f"http://127.0.0.1:{port}/v1",
            model="b3d-copilot-model",
            api_key="b3d-key",
            timeout_seconds=5.0,
        )
        provider = OpenAICompatibleProvider(config=config)
        queue = ThreadSafeEventQueue()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher, event_queue=queue)
        bridge = TimerBridge(runtime=runtime, event_queue=queue)

        # ---------------------------------------------------------------------
        # TEST 1: Single Tool Round-Trip (inspect_scene against real Blender scene)
        # ---------------------------------------------------------------------
        print("Running Test 1: Single Tool Round-Trip (inspect_scene)...")
        BlenderHeadlessFakeServerHandler.request_history.clear()

        turn_id = runtime.submit_prompt("Sahneyi incele")
        assert runtime.current_state == AgentState.PROCESSING

        done = pump_timer_until_idle(bridge, runtime, timeout=3.0)
        assert done, "Timeout waiting for Test 1 to complete"
        assert runtime.current_state == AgentState.IDLE
        assert runtime.last_result is not None
        assert runtime.last_result.state == "IDLE"

        # Verify real tool executed on live Blender scene
        assert len(runtime.last_result.tool_results) == 1
        tr1 = runtime.last_result.tool_results[0]
        assert tr1.success is True
        assert tr1.tool == "inspect_scene"
        assert tr1.data["scene_name"] == "Scene"
        assert tr1.data["counts"]["total"] >= 3  # Cube, Light, Camera

        # Verify final assistant response synthesised the real tool result
        assert "Sahne başarıyla incelendi" in runtime.last_result.final_text
        assert str(tr1.data["counts"]["total"]) in runtime.last_result.final_text

        # Verify 2 HTTP requests were made to fake server
        assert len(BlenderHeadlessFakeServerHandler.request_history) == 2

        # Verify conversation sequence integrity
        runtime.conversation.validate_sequence()
        assert len(runtime.conversation.messages) == 4
        print("[PASS] Test 1: Real inspect_scene tool round-trip verified.")

        # ---------------------------------------------------------------------
        # TEST 2: Single Tool Round-Trip for inspect_object (Cube)
        # ---------------------------------------------------------------------
        print("Running Test 2: inspect_object tool round-trip on Cube...")
        runtime.clear_history()
        BlenderHeadlessFakeServerHandler.request_history.clear()

        turn_id2 = runtime.submit_prompt("Cube objesini incele")
        done2 = pump_timer_until_idle(bridge, runtime, timeout=3.0)
        assert done2, "Timeout waiting for Test 2 to complete"
        assert runtime.current_state == AgentState.IDLE

        assert len(runtime.last_result.tool_results) == 1
        tr2 = runtime.last_result.tool_results[0]
        assert tr2.success is True
        assert tr2.tool == "inspect_object"
        assert tr2.data["name"] == "Cube"
        assert tr2.data["type"] == "MESH"
        assert "Cube" in runtime.last_result.final_text
        print("[PASS] Test 2: inspect_object on live Cube verified.")

        # ---------------------------------------------------------------------
        # TEST 3: Multi-Round Sequential Tool Flow (inspect_scene -> inspect_object)
        # ---------------------------------------------------------------------
        print("Running Test 3: Multi-Round Sequential Tool Flow...")
        runtime.clear_history()
        BlenderHeadlessFakeServerHandler.request_history.clear()

        turn_id3 = runtime.submit_prompt("Çoklu inceleme")
        done3 = pump_timer_until_idle(bridge, runtime, timeout=4.0)
        assert done3, "Timeout waiting for Test 3 to complete"
        assert runtime.current_state == AgentState.IDLE

        assert len(runtime.last_result.tool_results) == 2
        assert runtime.last_result.tool_results[0].tool == "inspect_scene"
        assert runtime.last_result.tool_results[1].tool == "inspect_object"
        assert len(BlenderHeadlessFakeServerHandler.request_history) == 3
        print("[PASS] Test 3: Multi-round sequential tool flow verified.")

        # ---------------------------------------------------------------------
        # TEST 4: Cleanup and shutdown
        # ---------------------------------------------------------------------
        runtime.shutdown()
        print("[PASS] Test 4: Clean runtime shutdown verified.")

        print("=== M2.7 REAL PROVIDER + TOOL ROUND-TRIP TEST COMPLETED SUCCESSFULLY ===\n")

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        run_tests()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        sys.exit(1)
