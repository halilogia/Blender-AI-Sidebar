"""Set polygon shading (SMOOTH/FLAT) on a mesh object in the active Blender scene."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class SetShadingTool(BaseTool):
    """Sets polygon shading mode (SMOOTH or FLAT) on a target mesh object."""

    name = "set_shading"
    description = (
        "Set smooth or flat polygon shading on all faces of a target mesh object in the Blender scene. "
        "Every change records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the target mesh object.",
            },
            "shading": {
                "type": "string",
                "enum": ["SMOOTH", "FLAT"],
                "description": "Shading mode to apply: 'SMOOTH' or 'FLAT'.",
            },
        },
        "required": ["name", "shading"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute set_shading via the adapter."""
        return adapter.set_shading(
            name=kwargs.get("name"),
            shading=kwargs.get("shading"),
        )
