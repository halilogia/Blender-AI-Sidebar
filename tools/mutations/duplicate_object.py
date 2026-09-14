"""Duplicate an existing object with independent data and optional transforms."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class DuplicateObjectTool(BaseTool):
    """Duplicates an existing object in Blender with independent data and optional transforms."""

    name = "duplicate_object"
    description = (
        "Duplicate an existing Blender object along with its data block and attached materials, "
        "with an optional new name and optional location, rotation, or scale transforms. "
        "Records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "source_name": {
                "type": "string",
                "description": "Name of the existing object in the scene to duplicate.",
            },
            "new_name": {
                "type": "string",
                "description": (
                    "Optional custom name for the duplicate object. Must not already exist in the scene. "
                    "If omitted, a deterministic name '{source_name}_copy_{n}' is generated."
                ),
            },
            "location": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Optional target [X, Y, Z] world position coordinates in meters. Defaults to source location.",
            },
            "rotation": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Optional target [X, Y, Z] Euler rotation angles in radians. Defaults to source rotation.",
            },
            "scale": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Optional target [X, Y, Z] scale multipliers. Defaults to source scale.",
            },
        },
        "required": ["source_name"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute object duplication via the adapter."""
        source_name = kwargs.get("source_name")
        new_name = kwargs.get("new_name")
        location = kwargs.get("location")
        rotation = kwargs.get("rotation")
        scale = kwargs.get("scale")

        return adapter.duplicate_object(
            source_name=source_name,
            new_name=new_name,
            location=location,
            rotation=rotation,
            scale=scale,
        )
