"""Deterministic MockProvider for testing the agent execution loop without an external LLM.

Zero Blender dependencies. Pure Python.
"""

from typing import List, Optional

from core.types import ToolResult
from agent.models import ProviderResponse, ToolCall
from agent.provider import BaseProvider


class MockProvider(BaseProvider):
    """Deterministic provider mapping known prompt intents to tool calls and final summaries."""

    def generate(
        self,
        prompt: str,
        tool_results: Optional[List[ToolResult]] = None,
    ) -> ProviderResponse:
        """Process prompt or format final summary based on tool results."""
        # 1. If tool results are provided, synthesize the final assistant response
        if tool_results:
            summaries = []
            for tr in tool_results:
                if not tr.success:
                    summaries.append(f"Araç hatası ({tr.tool}): {tr.error.message}")
                elif tr.tool == "inspect_scene":
                    total = tr.data.get("counts", {}).get("total", 0)
                    summaries.append(f"Sahne incelemesi tamamlandı. Toplam {total} nesne bulundu.")
                elif tr.tool == "inspect_selection":
                    act = tr.data.get("active_object") or "Yok"
                    count = tr.data.get("selection_count", 0)
                    summaries.append(f"Seçim incelemesi tamamlandı. Aktif nesne: {act}, Toplam seçim: {count}.")
                elif tr.tool == "inspect_object":
                    name = tr.data.get("name")
                    obj_type = tr.data.get("type")
                    summaries.append(f"Obje incelemesi tamamlandı: {name} (Tip: {obj_type}).")
                elif tr.tool == "inspect_material":
                    mat = tr.data.get("material_name") or "Atanmamış"
                    summaries.append(f"Malzeme incelemesi tamamlandı: {mat}.")
                elif tr.tool == "inspect_mesh":
                    mesh = tr.data.get("mesh_name")
                    v = tr.data.get("counts", {}).get("vertices", 0)
                    p = tr.data.get("counts", {}).get("polygons", 0)
                    summaries.append(f"Mesh incelemesi tamamlandı: {mesh} ({v} vertex, {p} poligon).")
                else:
                    summaries.append(f"İşlem tamamlandı ({tr.tool}).")

            final_text = " ".join(summaries)
            return ProviderResponse(assistant_text=final_text, tool_calls=[], is_final=True)

        # 2. Initial prompt processing -> tool call mapping
        clean_prompt = prompt.strip()

        if clean_prompt == "Mevcut sahneyi incele":
            return ProviderResponse(
                assistant_text="Sahne inceleniyor...",
                tool_calls=[ToolCall(call_id="call_mock_scene", tool_name="inspect_scene", arguments={})],
                is_final=False,
            )

        if clean_prompt == "Seçimi incele":
            return ProviderResponse(
                assistant_text="Seçim inceleniyor...",
                tool_calls=[ToolCall(call_id="call_mock_selection", tool_name="inspect_selection", arguments={})],
                is_final=False,
            )

        if clean_prompt == "Cube'u incele":
            return ProviderResponse(
                assistant_text="Cube nesnesi inceleniyor...",
                tool_calls=[ToolCall(call_id="call_mock_object", tool_name="inspect_object", arguments={"name": "Cube"})],
                is_final=False,
            )

        if clean_prompt == "Material'ı incele":
            return ProviderResponse(
                assistant_text="Material inceleniyor...",
                tool_calls=[ToolCall(call_id="call_mock_material", tool_name="inspect_material", arguments={"material_name": "Material"})],
                is_final=False,
            )

        if clean_prompt == "Cube mesh'ini incele":
            return ProviderResponse(
                assistant_text="Cube mesh geometrisi inceleniyor...",
                tool_calls=[ToolCall(call_id="call_mock_mesh", tool_name="inspect_mesh", arguments={"object_name": "Cube"})],
                is_final=False,
            )

        # Multi-tool sequence test
        if clean_prompt == "Tam inceleme":
            return ProviderResponse(
                assistant_text="Sahne ve seçim inceleniyor...",
                tool_calls=[
                    ToolCall(call_id="call_mock_seq_1", tool_name="inspect_scene", arguments={}),
                    ToolCall(call_id="call_mock_seq_2", tool_name="inspect_selection", arguments={}),
                ],
                is_final=False,
            )

        # Unknown prompt fallback
        return ProviderResponse(
            assistant_text="Anlaşılmayan istek: İlgili grounding aracı bulunamadı.",
            tool_calls=[],
            is_final=True,
        )
