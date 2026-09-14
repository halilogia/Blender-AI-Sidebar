"""End-to-End Agentic Acceptance Test Suite for Blender AI Sidebar on Blender 5.2.1 LTS.

M9 Task 7: Proves the complete multi-round agentic execution loop on a real Blender scene:
User Prompt -> LLM response -> Grounding (inspect_scene) -> propose_plan -> PlanReview
-> Approval -> PlanExecutor -> Real Blender Mutations -> ChangeVerifier -> Tool Feedback
-> Final LLM Response.

Also proves the self-repair loop on step failure without recreating already-successful mutations.
"""

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import bpy

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from adapter.blender_adapter import BlenderAdapter
from agent.dispatcher import ToolDispatcher
from agent.models import ChatMessage, ProviderResponse, Role, ToolCall
from agent.plan_models import PlanStatus
from agent.policy import ApprovalPolicy, InvalidApprovalError, NoPendingApprovalError
from agent.provider import BaseProvider
from agent.runtime import AgentRuntime
from agent.state_machine import AgentState
from agent.verifier import ChangeVerifier
from agent.worker import AgentWorker
from core.event_queue import ThreadSafeEventQueue
from core.types import ToolResult
from tools.mutations.add_modifier import AddModifierTool
from tools.mutations.create_camera import CreateCameraTool
from tools.mutations.create_light import CreateLightTool
from tools.mutations.create_primitive import CreatePrimitiveTool
from tools.mutations.duplicate_object import DuplicateObjectTool
from tools.mutations.set_material import SetMaterialTool
from tools.mutations.set_shading import SetShadingTool
from tools.mutations.transform_object import TransformObjectTool
from tools.propose_plan import ProposePlanTool
from tools.read_only.inspect_scene import InspectSceneTool
from tools.registry import ToolRegistry
from ui.timer_bridge import TimerBridge


def clean_scene():
    """Reset the Blender scene to a completely empty state with global undo enabled."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True


def pump_timer_until(bridge: TimerBridge, condition_fn, timeout: float = 4.0) -> bool:
    """Pump timer ticks on the main thread until condition_fn returns True or timeout expires."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        bridge.tick()
        if condition_fn():
            return True
        time.sleep(0.01)
    return False


class ProductSceneMockProvider(BaseProvider):
    """Deterministic provider driving the Product Scene E2E flow:

    Turn 1: Inspects scene for grounding.
    Turn 2: Proposes structured 7-step execution plan using semantic tools.
    Turn 3: Synthesizes final assistant response upon plan completion.
    """

    def __init__(self):
        self.call_count = 0

    def generate(
        self,
        prompt: str,
        tool_results: Optional[List[ToolResult]] = None,
    ) -> ProviderResponse:
        self.call_count += 1

        # Turn 1: Initial prompt -> Request scene grounding inspection
        if not tool_results:
            return ProviderResponse(
                assistant_text="Sahne durumunu inceliyorum...",
                tool_calls=[
                    ToolCall(call_id="call_inspect_1", tool_name="inspect_scene", arguments={})
                ],
                is_final=False,
            )

        # Check latest tool result
        last_result = tool_results[-1]

        # Turn 2: After inspect_scene -> Propose structured product scene plan
        if last_result.tool == "inspect_scene":
            plan_arguments = {
                "title": "Ürün Sahnesi Kurulum Planı",
                "description": (
                    "Küp stand oluştur, üstüne ikinci küpü kopyala, kenarlarını bevel ile yumuşat, "
                    "smooth shading uygula, kırmızı materyal ata, kamera ve stüdyo ışığı oluştur."
                ),
                "steps": [
                    {
                        "step_id": "step_stand",
                        "tool_name": "create_primitive",
                        "arguments": {
                            "primitive_type": "CUBE",
                            "name": "Stand",
                            "location": [0.0, 0.0, 0.0],
                            "scale": [2.0, 2.0, 0.2],
                        },
                        "description": "Taban stand küpü oluştur",
                        "depends_on": [],
                    },
                    {
                        "step_id": "step_product",
                        "tool_name": "duplicate_object",
                        "arguments": {
                            "source_name": "Stand",
                            "new_name": "ProductCube",
                            "location": [0.0, 0.0, 1.2],
                            "scale": [1.0, 1.0, 1.0],
                        },
                        "description": "Standdan ürün küpünü çoğalt ve yukarı taşı",
                        "depends_on": ["step_stand"],
                    },
                    {
                        "step_id": "step_bevel",
                        "tool_name": "add_modifier",
                        "arguments": {
                            "name": "ProductCube",
                            "modifier_type": "BEVEL",
                            "width": 0.1,
                            "segments": 3,
                        },
                        "description": "Ürün küpünün kenarlarına Bevel uygula",
                        "depends_on": ["step_product"],
                    },
                    {
                        "step_id": "step_shading",
                        "tool_name": "set_shading",
                        "arguments": {
                            "name": "ProductCube",
                            "shading": "SMOOTH",
                        },
                        "description": "Ürün küpüne yumuşak gölgelendirme uygula",
                        "depends_on": ["step_product"],
                    },
                    {
                        "step_id": "step_material",
                        "tool_name": "set_material",
                        "arguments": {
                            "object_name": "ProductCube",
                            "material_name": "RedMat",
                            "base_color": [1.0, 0.0, 0.0, 1.0],
                            "roughness": 0.3,
                        },
                        "description": "Ürün küpüne kırmızı materyal ata",
                        "depends_on": ["step_product"],
                    },
                    {
                        "step_id": "step_camera",
                        "tool_name": "create_camera",
                        "arguments": {
                            "name": "ProductCamera",
                            "location": [4.0, -5.0, 3.5],
                            "rotation": [1.1, 0.0, 0.8],
                            "lens": 50.0,
                            "make_active": True,
                        },
                        "description": "Ürün kamerasını oluştur ve aktif yap",
                        "depends_on": [],
                    },
                    {
                        "step_id": "step_light",
                        "tool_name": "create_light",
                        "arguments": {
                            "name": "StudioLight",
                            "light_type": "AREA",
                            "location": [3.0, -3.0, 5.0],
                            "energy": 1000.0,
                        },
                        "description": "Stüdyo aydınlatma ışığını oluştur",
                        "depends_on": [],
                    },
                ],
            }
            return ProviderResponse(
                assistant_text="Ürün sahnesi için yürütme planı hazırladım.",
                tool_calls=[
                    ToolCall(call_id="call_plan_1", tool_name="propose_plan", arguments=plan_arguments)
                ],
                is_final=False,
            )

        # Turn 3: After plan execution completes -> Emit final assistant summary
        if last_result.tool == "propose_plan":
            return ProviderResponse(
                assistant_text=(
                    "Ürün sahnesi başarıyla oluşturuldu: Stand ve ürün küpü yerleştirildi, "
                    "bevel modifier ve smooth shading uygulandı, kırmızı materyal atandı, "
                    "kamera ve stüdyo ışığı eklendi."
                ),
                tool_calls=[],
                is_final=True,
            )

        return ProviderResponse(
            assistant_text="Beklenmeyen durum.",
            tool_calls=[],
            is_final=True,
        )


class RepairMockProvider(BaseProvider):
    """Deterministic provider driving the Plan Repair flow:

    Turn 1: Proposes a 2-step plan where step 2 is intentionally invalid (targets missing object).
    Turn 2: Receives PLAN_EXECUTION_FAILED tool feedback; recognizes step 1 succeeded and
            proposes a targeted repair plan with ONLY step 2 repaired (does NOT recreate step 1).
    Turn 3: Emits final response upon repair completion.
    """

    def __init__(self):
        self.call_count = 0

    def generate(
        self,
        prompt: str,
        tool_results: Optional[List[ToolResult]] = None,
    ) -> ProviderResponse:
        self.call_count += 1

        # Turn 1: Propose initial plan with deliberate step 2 failure
        if not tool_results:
            return ProviderResponse(
                assistant_text="Küp oluşturma ve taşıma planı hazırlıyorum...",
                tool_calls=[
                    ToolCall(
                        call_id="call_plan_initial",
                        tool_name="propose_plan",
                        arguments={
                            "title": "Initial Cube Plan",
                            "description": "Create cube and attempt transform on invalid target",
                            "steps": [
                                {
                                    "step_id": "step_create",
                                    "tool_name": "create_primitive",
                                    "arguments": {
                                        "primitive_type": "CUBE",
                                        "name": "RepairCube",
                                        "location": [0.0, 0.0, 0.0],
                                    },
                                    "description": "Create base cube",
                                    "depends_on": [],
                                },
                                {
                                    "step_id": "step_fail",
                                    "tool_name": "transform_object",
                                    "arguments": {
                                        "name": "GhostTargetObject",
                                        "location": [5.0, 5.0, 5.0],
                                    },
                                    "description": "Attempt transform on non-existent object",
                                    "depends_on": ["step_create"],
                                },
                            ],
                        },
                    )
                ],
                is_final=False,
            )

        last_result = tool_results[-1]

        # Turn 2: Plan failed -> Propose targeted repair plan (only step 2 with correct target)
        if last_result.tool == "propose_plan" and not last_result.success:
            return ProviderResponse(
                assistant_text="Plan başarısız oldu. Hatalı adımı onarıyorum...",
                tool_calls=[
                    ToolCall(
                        call_id="call_plan_repair",
                        tool_name="propose_plan",
                        arguments={
                            "title": "Repair Plan: Transform RepairCube",
                            "description": "Transform the successfully created RepairCube to target position",
                            "steps": [
                                {
                                    "step_id": "step_repair_transform",
                                    "tool_name": "transform_object",
                                    "arguments": {
                                        "name": "RepairCube",
                                        "location": [5.0, 0.0, 0.0],
                                    },
                                    "description": "Transform RepairCube to X=5.0",
                                    "depends_on": [],
                                },
                            ],
                        },
                    )
                ],
                is_final=False,
            )

        # Turn 3: Repair completed -> Final summary
        if last_result.tool == "propose_plan" and last_result.success:
            return ProviderResponse(
                assistant_text="Onarım planı tamamlandı: RepairCube başarıyla taşındı.",
                tool_calls=[],
                is_final=True,
            )

        return ProviderResponse(
            assistant_text="Bilinmeyen durum.",
            tool_calls=[],
            is_final=True,
        )


def setup_test_runtime(provider: BaseProvider):
    """Build fully wired AgentRuntime with all semantic tools, verifiers, and timer bridge."""
    registry = ToolRegistry()
    registry.register(InspectSceneTool())
    registry.register(CreatePrimitiveTool())
    registry.register(DuplicateObjectTool())
    registry.register(AddModifierTool())
    registry.register(SetShadingTool())
    registry.register(SetMaterialTool())
    registry.register(CreateCameraTool())
    registry.register(CreateLightTool())
    registry.register(TransformObjectTool())
    registry.register(ProposePlanTool())

    adapter = BlenderAdapter()
    dispatcher = ToolDispatcher(registry=registry, adapter=adapter)
    verifier = ChangeVerifier()
    policy = ApprovalPolicy()
    event_queue = ThreadSafeEventQueue()
    worker = AgentWorker(provider=provider, event_queue=event_queue)

    runtime = AgentRuntime(
        provider=provider,
        dispatcher=dispatcher,
        event_queue=event_queue,
        worker=worker,
        policy=policy,
        verifier=verifier,
    )
    bridge = TimerBridge(runtime=runtime, event_queue=event_queue)
    return runtime, bridge, adapter, worker, registry


def test_end_to_end_product_scene():
    """E2E Scenario 1: Full product scene creation via agentic plan approval loop."""
    print("\n--- Running E2E Scenario 1: Product Scene Acceptance ---")
    clean_scene()

    provider = ProductSceneMockProvider()
    runtime, bridge, adapter, worker, registry = setup_test_runtime(provider)

    try:
        # 1. User submits prompt
        prompt = (
            "Basit bir ürün sahnesi oluştur: bir küp stand oluştur, üstüne ikinci bir küp yerleştir, "
            "ikinci küpün kenarlarını yumuşat, materyalini kırmızı yap, kamera oluştur ve sahneyi aydınlat."
        )
        turn_id = runtime.submit_prompt(prompt)
        assert turn_id == "turn_1"
        assert runtime.current_state == AgentState.PROCESSING

        # 2. Pump timer through inspect_scene grounding until propose_plan halts at PENDING_APPROVAL
        pump_ok = pump_timer_until(
            bridge,
            lambda: runtime.current_state == AgentState.PENDING_APPROVAL,
            timeout=4.0,
        )
        assert pump_ok, f"Agent failed to reach PENDING_APPROVAL, current state: {runtime.current_state}"
        assert runtime.pending_plan_review is not None
        review = runtime.pending_plan_review
        assert review.title == "Ürün Sahnesi Kurulum Planı"
        assert len(review.steps) == 7

        # 3. Assert zero mutations have occurred in Blender scene BEFORE approval
        assert "Stand" not in bpy.data.objects, "Stand must not exist prior to approval"
        assert "ProductCube" not in bpy.data.objects, "ProductCube must not exist prior to approval"
        assert "ProductCamera" not in bpy.data.objects, "ProductCamera must not exist prior to approval"
        assert "StudioLight" not in bpy.data.objects, "StudioLight must not exist prior to approval"

        # 4. User Approves the plan
        approval_id = review.approval_id
        summary = runtime.approve_plan(approval_id)
        assert summary is not None
        assert summary.status == PlanStatus.COMPLETED
        assert summary.steps_completed == 7
        assert summary.steps_total == 7

        # 5. Pump timer until agent reaches IDLE with final response
        idle_ok = pump_timer_until(
            bridge,
            lambda: runtime.current_state == AgentState.IDLE and runtime.current_turn_id is None,
            timeout=4.0,
        )
        assert idle_ok, f"Agent failed to reach IDLE, current state: {runtime.current_state}"

        # 6. Verify real Blender scene state
        # 6.1 Mesh objects exist
        assert "Stand" in bpy.data.objects, "'Stand' must exist in scene"
        assert "ProductCube" in bpy.data.objects, "'ProductCube' must exist in scene"
        stand_obj = bpy.data.objects["Stand"]
        prod_obj = bpy.data.objects["ProductCube"]

        # 6.2 Distinct object and data identities
        assert stand_obj != prod_obj, "Stand and ProductCube must be distinct objects"
        assert stand_obj.data != prod_obj.data, "Stand and ProductCube must have independent mesh datablocks"

        # 6.3 Red material assigned to ProductCube
        assert len(prod_obj.material_slots) >= 1, "ProductCube must have a material slot"
        assigned_mat = prod_obj.material_slots[0].material
        assert assigned_mat is not None, "ProductCube material slot must not be empty"
        assert assigned_mat.name == "RedMat"
        assert assigned_mat.node_tree is not None
        bsdf = next((n for n in assigned_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        assert bsdf is not None, "Principled BSDF node must exist in material node tree"
        color = list(bsdf.inputs["Base Color"].default_value)
        assert round(color[0], 2) == 1.0 and round(color[1], 2) == 0.0 and round(color[2], 2) == 0.0

        # 6.4 Bevel modifier exists on ProductCube with valid parameters
        assert "Bevel" in prod_obj.modifiers, "ProductCube must have 'Bevel' modifier"
        bevel_mod = prod_obj.modifiers["Bevel"]
        assert bevel_mod.type == "BEVEL"
        assert round(bevel_mod.width, 2) == 0.1
        assert bevel_mod.segments == 3

        # 6.5 Smooth shading is applied to ProductCube
        assert len(prod_obj.data.polygons) > 0
        assert all(p.use_smooth for p in prod_obj.data.polygons), "ProductCube polygons must have smooth shading enabled"

        # 6.6 Camera created and active
        assert "ProductCamera" in bpy.data.objects, "'ProductCamera' must exist in scene"
        cam_obj = bpy.data.objects["ProductCamera"]
        assert cam_obj.type == "CAMERA"
        assert bpy.context.scene.camera == cam_obj, "ProductCamera must be the active scene camera"

        # 6.7 Studio light created
        assert "StudioLight" in bpy.data.objects, "'StudioLight' must exist in scene"
        light_obj = bpy.data.objects["StudioLight"]
        assert light_obj.type == "LIGHT"
        assert light_obj.data.type == "AREA"
        assert light_obj.data.energy >= 1000.0

        # 7. Verify Conversation message sequence & tool_calls matching
        messages = runtime.conversation.messages
        # Sequence: USER -> ASSISTANT (tool_call: inspect_scene) -> TOOL (inspect_scene)
        #          -> ASSISTANT (tool_call: propose_plan) -> TOOL (propose_plan) -> ASSISTANT (final)
        assert len(messages) >= 6
        assert messages[0].role == Role.USER
        assert messages[1].role == Role.ASSISTANT
        assert messages[1].tool_calls[0].tool_name == "inspect_scene"
        assert messages[2].role == Role.TOOL
        assert messages[2].tool_call_id == messages[1].tool_calls[0].call_id
        assert messages[3].role == Role.ASSISTANT
        assert messages[3].tool_calls[0].tool_name == "propose_plan"
        assert messages[4].role == Role.TOOL
        assert messages[4].tool_call_id == messages[3].tool_calls[0].call_id
        assert messages[5].role == Role.ASSISTANT
        assert not messages[5].tool_calls
        assert "başarıyla oluşturuldu" in messages[5].content
        assert runtime.last_result is not None
        assert "başarıyla oluşturuldu" in runtime.last_result.final_text

        # 8. Verify Idempotency: duplicate approval is rejected
        try:
            runtime.approve_plan(approval_id)
            assert False, "Duplicate approve_plan must raise InvalidApprovalError or NoPendingApprovalError"
        except (InvalidApprovalError, NoPendingApprovalError):
            pass

        print("[PASS] E2E Scenario 1: Full product scene creation and verification passed.")

    finally:
        worker.stop()


def test_end_to_end_repair_flow():
    """E2E Scenario 2: Self-repair loop on step failure without duplicate mutations."""
    print("\n--- Running E2E Scenario 2: Self-Repair Acceptance ---")
    clean_scene()

    provider = RepairMockProvider()
    runtime, bridge, adapter, worker, registry = setup_test_runtime(provider)

    try:
        # 1. Submit prompt
        turn_id = runtime.submit_prompt("Küp oluştur ve taşı")
        assert turn_id == "turn_1"

        # 2. Pump until initial plan reaches PENDING_APPROVAL
        pump_ok = pump_timer_until(
            bridge,
            lambda: runtime.current_state == AgentState.PENDING_APPROVAL,
            timeout=4.0,
        )
        assert pump_ok
        initial_review = runtime.pending_plan_review
        assert initial_review.title == "Initial Cube Plan"

        # 3. Approve initial plan (which has step 1: create RepairCube, step 2: fail on GhostTargetObject)
        initial_aid = initial_review.approval_id
        initial_summary = runtime.approve_plan(initial_aid)
        assert initial_summary is not None
        assert initial_summary.status == PlanStatus.FAILED
        assert initial_summary.steps_completed == 1

        # Assert step 1 succeeded: RepairCube was created in Blender
        assert "RepairCube" in bpy.data.objects, "RepairCube must have been created by step 1"

        # 4. Pump timer: Failure feedback is sent to provider, provider proposes REPAIR plan, halts at PENDING_APPROVAL
        repair_pump_ok = pump_timer_until(
            bridge,
            lambda: runtime.current_state == AgentState.PENDING_APPROVAL,
            timeout=4.0,
        )
        assert repair_pump_ok, f"Agent failed to reach PENDING_APPROVAL for repair plan, state: {runtime.current_state}"
        repair_review = runtime.pending_plan_review
        assert repair_review is not None
        assert "Repair" in repair_review.title

        # CRITICAL REPAIR INVARIANT: Repair plan must NOT recreate RepairCube
        repair_tools = [s.tool_name for s in repair_review.steps]
        assert "create_primitive" not in repair_tools, "Repair plan must NOT duplicate or recreate existing objects"
        assert repair_tools == ["transform_object"]

        # 5. User approves repair plan
        repair_aid = repair_review.approval_id
        repair_summary = runtime.approve_plan(repair_aid)
        assert repair_summary is not None
        assert repair_summary.status == PlanStatus.COMPLETED

        # 6. Pump timer until agent reaches IDLE with final response
        idle_ok = pump_timer_until(
            bridge,
            lambda: runtime.current_state == AgentState.IDLE and runtime.current_turn_id is None,
            timeout=4.0,
        )
        assert idle_ok

        # 7. Verify Blender scene state after repair
        # 7.1 Exactly one RepairCube exists in scene (not duplicated)
        cubes = [o.name for o in bpy.data.objects if "RepairCube" in o.name]
        assert len(cubes) == 1, f"Expected exactly 1 RepairCube in scene, found {cubes}"

        # 7.2 RepairCube was transformed to target position [5.0, 0.0, 0.0]
        cube_obj = bpy.data.objects["RepairCube"]
        assert round(cube_obj.location.x, 2) == 5.0
        assert round(cube_obj.location.y, 2) == 0.0
        assert round(cube_obj.location.z, 2) == 0.0

        # 7.3 Final response is present
        assert runtime.last_result is not None
        assert "Onarım planı tamamlandı" in runtime.last_result.final_text

        print("[PASS] E2E Scenario 2: Self-repair flow verified without duplicate mutations.")

    finally:
        worker.stop()


def run_all():
    print("\n========================================================")
    print("   RUNNING M9 TASK 7 E2E AGENTIC BLENDER ACCEPTANCE     ")
    print("========================================================\n")

    test_end_to_end_product_scene()
    test_end_to_end_repair_flow()

    print("\n========================================================")
    print("   ALL M9 TASK 7 E2E ACCEPTANCE TESTS PASSED (2/2)      ")
    print("========================================================\n")


if __name__ == "__main__":
    try:
        run_all()
    except Exception as e:
        print(f"\n[FAIL] E2E Acceptance test suite failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
