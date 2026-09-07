"""Headless integration test for Phase 5 full execution chain on Blender 5.2.1 LTS.

Tests:
MockProvider -> AgentRuntime -> ToolDispatcher -> BlenderAdapter -> Real bpy -> ToolResult -> Final Response
for all 5 grounding tools plus multi-tool sequences.
"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bpy

from adapter.blender_adapter import BlenderAdapter
from tools.registry import ToolRegistry
from tools.read_only.inspect_scene import InspectSceneTool
from tools.read_only.inspect_selection import InspectSelectionTool
from tools.read_only.inspect_object import InspectObjectTool
from tools.read_only.inspect_material import InspectMaterialTool
from tools.read_only.inspect_mesh import InspectMeshTool
from agent.mock_provider import MockProvider
from agent.dispatcher import ToolDispatcher
from agent.runtime import AgentRuntime


def setup_real_agent_runtime():
    """Wire real grounding tools and adapter with MockProvider and Dispatcher."""
    registry = ToolRegistry()
    registry.register(InspectSceneTool())
    registry.register(InspectSelectionTool())
    registry.register(InspectObjectTool())
    registry.register(InspectMaterialTool())
    registry.register(InspectMeshTool())

    adapter = BlenderAdapter()
    dispatcher = ToolDispatcher(registry=registry, adapter=adapter)
    provider = MockProvider()
    runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

    return runtime, registry, adapter


def test_chain_inspect_scene(runtime):
    """Test full cycle for inspect_scene."""
    result = runtime.run("Mevcut sahneyi incele")

    assert result.state == "IDLE", f"Unexpected terminal state: {result.state}"
    assert len(result.tool_results) == 1
    tr = result.tool_results[0]
    assert tr.tool == "inspect_scene"
    assert tr.success is True
    assert tr.data["scene_name"] == "Scene"
    assert "Sahne incelemesi tamamlandı" in result.final_text
    print("[PASS] Full execution chain for 'inspect_scene' verified.")


def test_chain_inspect_selection(runtime):
    """Test full cycle for inspect_selection with active Cube."""
    cube = bpy.data.objects.get("Cube")
    if cube:
        bpy.context.view_layer.objects.active = cube
        cube.select_set(True)

    result = runtime.run("Seçimi incele")

    assert result.state == "IDLE"
    assert len(result.tool_results) == 1
    tr = result.tool_results[0]
    assert tr.tool == "inspect_selection"
    assert tr.success is True
    assert tr.data["active_object"] == "Cube"
    assert "Seçim incelemesi tamamlandı. Aktif nesne: Cube" in result.final_text
    print("[PASS] Full execution chain for 'inspect_selection' verified.")


def test_chain_inspect_object(runtime):
    """Test full cycle for inspect_object on Cube."""
    result = runtime.run("Cube'u incele")

    assert result.state == "IDLE"
    assert len(result.tool_results) == 1
    tr = result.tool_results[0]
    assert tr.tool == "inspect_object"
    assert tr.success is True
    assert tr.data["name"] == "Cube"
    assert tr.data["type"] == "MESH"
    assert "Obje incelemesi tamamlandı: Cube (Tip: MESH)." in result.final_text
    print("[PASS] Full execution chain for 'inspect_object' verified.")


def test_chain_inspect_material(runtime):
    """Test full cycle for inspect_material."""
    result = runtime.run("Material'ı incele")

    assert result.state == "IDLE"
    assert len(result.tool_results) == 1
    tr = result.tool_results[0]
    assert tr.tool == "inspect_material"
    assert tr.success is True
    assert tr.data["material_name"] == "Material"
    assert "Malzeme incelemesi tamamlandı: Material." in result.final_text
    print("[PASS] Full execution chain for 'inspect_material' verified.")


def test_chain_inspect_mesh(runtime):
    """Test full cycle for inspect_mesh on Cube."""
    result = runtime.run("Cube mesh'ini incele")

    assert result.state == "IDLE"
    assert len(result.tool_results) == 1
    tr = result.tool_results[0]
    assert tr.tool == "inspect_mesh"
    assert tr.success is True
    assert tr.data["mesh_name"] == "Cube"
    assert tr.data["counts"]["vertices"] == 8
    assert tr.data["counts"]["polygons"] == 6
    assert "Mesh incelemesi tamamlandı: Cube (8 vertex, 6 poligon)." in result.final_text
    print("[PASS] Full execution chain for 'inspect_mesh' verified.")


def test_chain_sequential_tools(runtime):
    """Test full cycle for multi-tool sequence: Tam inceleme (scene + selection)."""
    result = runtime.run("Tam inceleme")

    assert result.state == "IDLE"
    assert len(result.tool_results) == 2

    tr_scene, tr_sel = result.tool_results
    assert tr_scene.tool == "inspect_scene" and tr_scene.success is True
    assert tr_sel.tool == "inspect_selection" and tr_sel.success is True

    assert "Sahne incelemesi tamamlandı" in result.final_text
    assert "Seçim incelemesi tamamlandı" in result.final_text
    print("[PASS] Full execution chain for multi-tool sequence 'Tam inceleme' verified.")


if __name__ == "__main__":
    try:
        print("\n=== STARTING PHASE 5 MOCK AGENT FULL CHAIN INTEGRATION TEST ===")
        runtime, registry, adapter = setup_real_agent_runtime()

        test_chain_inspect_scene(runtime)
        test_chain_inspect_selection(runtime)
        test_chain_inspect_object(runtime)
        test_chain_inspect_material(runtime)
        test_chain_inspect_mesh(runtime)
        test_chain_sequential_tools(runtime)

        print("=== PHASE 5 MOCK AGENT TEST COMPLETED SUCCESSFULLY ===\n")
        sys.exit(0)
    except AssertionError as err:
        print(f"\n[FAIL] Assertion error: {err}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected exception: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
