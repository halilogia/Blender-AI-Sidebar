"""Inspect active Blender scene overview."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class InspectSceneTool(BaseTool):
    """Inspects the active Blender scene overview."""

    name = "inspect_scene"
    description = (
        "Inspect the active Blender scene overview, including collections, "
        "object counts, active/selected objects, and active camera."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute scene inspection via the adapter."""
        return adapter.inspect_scene()
