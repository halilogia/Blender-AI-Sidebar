"""Headless integration tests for Phase 6 Async Boundary and Event Loop on Blender 5.2.1 LTS.

Verifies:
1. Async mock scene inspection through worker -> queue -> timer pump -> main-thread tool execution.
2. Async mock object inspection.
3. Multi-tool async flow.
4. bpy.app.timers registration and unregistration.
5. Unregister while worker is active (clean shutdown without hanging).
6. In-flight cancellation.
7. Stale event rejection.
8. Worker thread cannot access BlenderAdapter (ThreadSafetyViolationError triggered).
9. Bounded queue draining / UI starvation prevention.
"""

import os
import sys
import time
import threading

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
from adapter.blender_adapter import BlenderAdapter, ThreadSafetyViolationError
from core.events import ProviderResponseReadyEvent, PromptSubmittedEvent
from core.event_queue import ThreadSafeEventQueue
from agent.mock_provider import MockProvider
from agent.models import ProviderResponse
from agent.dispatcher import ToolDispatcher
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from agent.worker import AgentWorker
from ui.timer_bridge import TimerBridge
from tools.registry import ToolRegistry
from tools.read_only.inspect_scene import InspectSceneTool
from tools.read_only.inspect_selection import InspectSelectionTool
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_material import InspectMaterialTool
from tools.read_only.inspect_mesh import InspectMeshTool


def setup_async_environment():
    """Create isolated async test environment with real adapter and read-only tools."""
    registry = ToolRegistry()
    registry.register(InspectSceneTool())
    registry.register(InspectSelectionTool())
    registry.register(InspectObjectTool())
    registry.register(InspectMaterialTool())
    registry.register(InspectMeshTool())

    adapter = BlenderAdapter()
    dispatcher = ToolDispatcher(registry=registry, adapter=adapter)
    provider = MockProvider()
    queue = ThreadSafeEventQueue()
    worker = AgentWorker(provider=provider, event_queue=queue)
    runtime = AgentRuntime(
        provider=provider,
        dispatcher=dispatcher,
        event_queue=queue,
        worker=worker,
    )
    bridge = TimerBridge(runtime=runtime, event_queue=queue)
    return runtime, bridge, adapter, worker


def pump_timer_until_idle(bridge: TimerBridge, runtime: AgentRuntime, timeout: float = 2.0) -> bool:
    """Pump timer ticks on the main thread until the agent reaches IDLE or times out."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        bridge.tick()
        if runtime.current_state == AgentState.IDLE and runtime.current_turn_id is None:
            return True
        time.sleep(0.01)
    return False


def test_async_scene_inspection():
    """Test 1: Async mock scene inspection."""
    runtime, bridge, adapter, worker = setup_async_environment()
    try:
        turn_id = runtime.submit_prompt("Mevcut sahneyi incele")
        assert turn_id == "turn_1"
        assert runtime.current_state == AgentState.PROCESSING

        success = pump_timer_until_idle(bridge, runtime)
        assert success, "Agent failed to reach IDLE state within timeout"

        res = runtime.last_result
        assert res is not None
        assert res.state == "IDLE"
        assert len(res.tool_results) == 1
        assert res.tool_results[0].tool == "inspect_scene"
        assert res.tool_results[0].success is True
        assert "Sahne incelemesi tamamlandı" in res.final_text
        assert runtime.current_metrics is not None
        assert runtime.current_metrics.total_duration_sec is not None
        print(f"[PASS] Async scene inspection verified (Turn duration: {runtime.current_metrics.total_duration_sec}s).")
    finally:
        runtime.shutdown()


def test_async_object_inspection():
    """Test 2: Async mock object inspection on Cube."""
    runtime, bridge, adapter, worker = setup_async_environment()
    try:
        turn_id = runtime.submit_prompt("Cube'u incele")
        success = pump_timer_until_idle(bridge, runtime)
        assert success, "Agent failed to reach IDLE state within timeout"

        res = runtime.last_result
        assert res is not None
        assert len(res.tool_results) == 1
        assert res.tool_results[0].tool == "inspect_object"
        assert res.tool_results[0].data["name"] == "Cube"
        assert "Obje incelemesi tamamlandı: Cube" in res.final_text
        print("[PASS] Async object inspection for 'Cube' verified.")
    finally:
        runtime.shutdown()


def test_async_multi_tool_flow():
    """Test 3: Async multi-tool sequence (Tam inceleme: scene + selection)."""
    runtime, bridge, adapter, worker = setup_async_environment()
    try:
        turn_id = runtime.submit_prompt("Tam inceleme")
        success = pump_timer_until_idle(bridge, runtime)
        assert success, "Agent failed to reach IDLE state within timeout"

        res = runtime.last_result
        assert res is not None
        assert len(res.tool_results) == 2
        tools = [tr.tool for tr in res.tool_results]
        assert tools == ["inspect_scene", "inspect_selection"]
        assert "Sahne incelemesi tamamlandı" in res.final_text
        assert "Seçim incelemesi tamamlandı" in res.final_text
        print("[PASS] Async multi-tool flow verified.")
    finally:
        runtime.shutdown()


def test_timer_registration_lifecycle():
    """Test 4: Verify bpy.app.timers register and unregister."""
    runtime, bridge, adapter, worker = setup_async_environment()
    try:
        assert not bridge.is_active
        bridge.register()
        assert bridge.is_active
        assert bpy.app.timers.is_registered(bridge._callback_ref)

        # Duplicate register should be a no-op returning False
        assert not bridge.register()

        bridge.unregister()
        assert not bridge.is_active
        assert not bpy.app.timers.is_registered(bridge._callback_ref)
        print("[PASS] bpy.app.timers registration and unregistration verified.")
    finally:
        runtime.shutdown()


def test_unregister_while_worker_active():
    """Test 5: Extension unregister while worker thread is active shuts down cleanly."""
    blender_ai_sidebar.register()
    try:
        active_runtime = blender_ai_sidebar.get_runtime()
        assert active_runtime is not None

        # Start a prompt task
        active_runtime.submit_prompt("Mevcut sahneyi incele")
        assert active_runtime.worker.is_running

        # Unregister immediately while task is underway
        blender_ai_sidebar.unregister()
        assert blender_ai_sidebar.get_runtime() is None
        assert blender_ai_sidebar.get_timer_bridge() is None
        print("[PASS] Clean unregister while worker active verified (no hang, no residuals).")
    finally:
        pass


def test_in_flight_cancellation():
    """Test 6: In-flight cancellation clears turn and ignores subsequent worker results."""
    runtime, bridge, adapter, worker = setup_async_environment()
    try:
        turn_id = runtime.submit_prompt("Mevcut sahneyi incele")
        assert runtime.current_turn_id == turn_id

        # Cancel immediately
        runtime.cancel_current_turn()
        assert runtime.current_turn_id is None
        assert runtime.current_state == AgentState.IDLE

        # Allow worker to finish background processing and pump bridge
        time.sleep(0.05)
        bridge.tick()

        # State should still be IDLE, turn remains cancelled
        assert runtime.current_state == AgentState.IDLE
        assert runtime.last_result is None
        print("[PASS] In-flight cancellation verified.")
    finally:
        runtime.shutdown()


def test_stale_event_rejected():
    """Test 7: Stale events from older turns are rejected without mutating active turn."""
    runtime, bridge, adapter, worker = setup_async_environment()
    try:
        turn_id = runtime.submit_prompt("Cube'u incele")
        assert turn_id == "turn_1"

        # Inject a rogue event from an old turn
        stale_resp = ProviderResponse(assistant_text="Stale text", is_final=True)
        stale_event = ProviderResponseReadyEvent(response=stale_resp, turn_id="turn_old_999")
        runtime.event_queue.put(stale_event)

        # Tick bridge
        bridge.tick()

        assert runtime.stale_events_count == 1
        assert runtime.current_turn_id == "turn_1"
        assert runtime.current_state == AgentState.PROCESSING

        # Complete legitimate turn
        pump_timer_until_idle(bridge, runtime)
        assert runtime.last_result is not None
        assert "Cube" in runtime.last_result.final_text
        print("[PASS] Stale event rejection verified.")
    finally:
        runtime.shutdown()


def test_worker_cannot_access_adapter():
    """Test 8: Worker thread attempting to access BlenderAdapter raises ThreadSafetyViolationError."""
    adapter = BlenderAdapter()
    violation_caught = threading.Event()

    def rogue_worker_target():
        try:
            # Should fail immediately due to assert_main_thread guard
            adapter.inspect_scene()
        except ThreadSafetyViolationError:
            violation_caught.set()

    t = threading.Thread(target=rogue_worker_target, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert violation_caught.is_set(), "BlenderAdapter failed to block non-main-thread access!"
    print("[PASS] Worker thread cannot access BlenderAdapter (ThreadSafetyViolationError enforced).")


def test_queue_starvation_prevention():
    """Test 9: Bounded queue draining prevents main thread starvation."""
    runtime, bridge, adapter, worker = setup_async_environment()
    bridge.max_events_per_tick = 5

    try:
        # Enqueue 20 events
        for i in range(20):
            runtime.event_queue.put(PromptSubmittedEvent(prompt=f"Flood_{i}", turn_id="turn_flood"))

        processed = bridge.tick()
        assert processed == 5, f"Expected exactly 5 processed events, got {processed}"
        assert runtime.event_queue.qsize() == 15, f"Expected 15 remaining in queue, got {runtime.event_queue.qsize()}"
        print("[PASS] Queue starvation prevention verified (bounded batch draining enforced).")
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    try:
        print("\n=== STARTING PHASE 6 ASYNC BOUNDARY & EVENT LOOP INTEGRATION TEST ===")
        test_async_scene_inspection()
        test_async_object_inspection()
        test_async_multi_tool_flow()
        test_timer_registration_lifecycle()
        test_unregister_while_worker_active()
        test_in_flight_cancellation()
        test_stale_event_rejected()
        test_worker_cannot_access_adapter()
        test_queue_starvation_prevention()

        print("=== PHASE 6 ASYNC BOUNDARY TEST COMPLETED SUCCESSFULLY ===\n")
        sys.exit(0)
    except AssertionError as err:
        print(f"\n[FAIL] Assertion error: {err}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected exception: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
