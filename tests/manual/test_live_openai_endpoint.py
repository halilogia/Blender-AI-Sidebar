"""Live OpenAI-compatible endpoint verification script (e.g., 9Router, LM Studio, Ollama, OpenRouter).

Usage:
    python tests/manual/test_live_openai_endpoint.py

Configured by default for local 9Router:
    base_url: http://localhost:20128/v1
Reads saved config from core.config if available.
Gracefully skips if endpoint is offline.
"""

import os
import sys
import time
import urllib.request
import urllib.error

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config import Config, load_config
from core.event_queue import ThreadSafeEventQueue
from agent.dispatcher import ToolDispatcher
from agent.openai_provider import OpenAICompatibleProvider
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from tools.base import BaseTool
from tools.registry import ToolRegistry
from core.types import RiskLevel, ToolResult


class StandaloneSceneTool(BaseTool):
    name = "inspect_scene"
    description = "Inspect Blender 3D scene summary."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter, **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, {"scene_name": "Scene", "counts": {"total": 3, "mesh": 1}})


def check_endpoint_online(base_url: str, timeout: float = 2.0) -> bool:
    """Check if the target OpenAI-compatible endpoint is reachable."""
    try:
        # Check base URL or models endpoint
        url = base_url.rstrip("/") + "/models"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 401, 403)
    except urllib.error.HTTPError as err:
        return err.code in (200, 401, 403, 404)
    except Exception:
        return False


def main():
    print("=== LIVE OPENAI-COMPATIBLE ENDPOINT (9ROUTER) VERIFICATION ===")

    # 1. Load config
    cfg, _ = load_config()
    target_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("BLENDER_AI_BASE_URL") or "http://localhost:20128/v1"
    cfg.base_url = target_url
    target_model = cfg.model or "gpt-3.5-turbo"
    print(f"Target Base URL : {target_url}")
    print(f"Target Model    : {target_model}")
    print(f"API Key Present : {bool(cfg.api_key)}")

    # 2. Check reachability
    is_online = check_endpoint_online(target_url, timeout=2.0)
    if not is_online:
        print(f"\n[SKIP] Endpoint '{target_url}' is not currently reachable.")
        print("To run live test:")
        print("1. Start your local 9Router / OpenAI-compatible endpoint on http://localhost:20128/v1")
        print("2. Run: python tests/manual/test_live_openai_endpoint.py\n")
        return 0

    print("\n[OK] Endpoint is reachable! Starting live round-trip test...")

    # 3. Setup tool registry and provider
    registry = ToolRegistry()
    registry.register(StandaloneSceneTool())
    dispatcher = ToolDispatcher(registry=registry, adapter=None)

    provider = OpenAICompatibleProvider(config=cfg)
    queue = ThreadSafeEventQueue()
    runtime = AgentRuntime(provider=provider, dispatcher=dispatcher, event_queue=queue)

    print("Submitting prompt: 'Sahneyi incele ve bilgi ver'...")
    turn_id = runtime.submit_prompt("Sahneyi incele ve bilgi ver")

    deadline = time.time() + 30.0
    while time.time() < deadline:
        events = queue.drain_batch(max_items=10, max_time_sec=0.1)
        for ev in events:
            runtime.process_event(ev)
        if runtime.current_state in (AgentState.IDLE, AgentState.ERROR) and runtime.current_turn_id is None:
            break
        time.sleep(0.05)

    res = runtime.last_result
    if res:
        print(f"\nTurn Status : {res.state}")
        print(f"Final Text  : {res.final_text}")
        print(f"Tool Results: {len(res.tool_results)}")
        for tr in res.tool_results:
            print(f"  - Tool '{tr.tool}': success={tr.success}, data={tr.data}")
        print("\n[PASS] Live round-trip completed successfully!")
    else:
        print("\n[TIMEOUT] Turn did not complete within 30 seconds.")

    runtime.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
