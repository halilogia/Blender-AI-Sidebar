"""Transform spatial properties (location, rotation, scale) of an object."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class TransformObjectTool(BaseTool):
    """Transforms an object's location, rotation, or scale in the Blender scene."""

    name = "transform_object"
    description = (
        "Modify the position, rotation (Euler XYZ in radians), or scale of a Blender object by name. "
        "Supports absolute coordinates or relative offsets. Every transform records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The exact name of the target object to transform.",
            },
            "location": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] world position or offset vector in meters.",
            },
            "rotation": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] Euler rotation angles or angular offset in radians.",
            },
            "scale": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] scale multipliers or factor.",
            },
            "relative": {
                "type": "boolean",
                "description": "If true, treats input vectors as relative offsets rather than absolute values. Default is false.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute object transformation via the adapter."""
        name = kwargs.get("name")
        location = kwargs.get("location")
        rotation = kwargs.get("rotation")
        scale = kwargs.get("scale")
        relative = bool(kwargs.get("relative", False))

        if location is None and rotation is None and scale is None:
            return ToolResult.fail(
                tool=self.name,
                error_type="INVALID_ARGUMENT",
                message="At least one transform property ('location', 'rotation', 'scale') must be provided.",
                details={"provided_args": list(kwargs.keys())},
            )

        return adapter.transform_object(
            name=name,
            location=location,
            rotation=rotation,
            scale=scale,
            relative=relative,
        )
