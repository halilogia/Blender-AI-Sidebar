"""Create a new geometric primitive object in the active Blender scene."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class CreatePrimitiveTool(BaseTool):
    """Creates a basic geometric primitive (Cube, Sphere, Plane) in the scene."""

    name = "create_primitive"
    description = (
        "Create a new geometric mesh primitive (CUBE, SPHERE, PLANE) in the Blender scene "
        "at an optional position, rotation, and scale. Every creation records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "primitive_type": {
                "type": "string",
                "enum": ["CUBE", "SPHERE", "PLANE"],
                "description": "The type of mesh primitive to create. Allowed: CUBE, SPHERE, PLANE.",
            },
            "name": {
                "type": "string",
                "description": "Optional custom name for the created object. Defaults to the primitive type.",
            },
            "location": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] world position coordinates in meters. Default is [0.0, 0.0, 0.0].",
            },
            "rotation": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] Euler rotation angles in radians. Default is [0.0, 0.0, 0.0].",
            },
            "scale": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] scale multipliers. Default is [1.0, 1.0, 1.0].",
            },
            "size": {
                "type": "number",
                "description": "Base size (diameter/dimension) in meters. Default is 2.0.",
            },
        },
        "required": ["primitive_type"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute primitive creation via the adapter."""
        primitive_type = kwargs.get("primitive_type")
        name = kwargs.get("name")
        location = kwargs.get("location")
        rotation = kwargs.get("rotation")
        scale = kwargs.get("scale")
        size = kwargs.get("size")

        return adapter.create_primitive(
            primitive_type=primitive_type,
            name=name,
            location=location,
            rotation=rotation,
            scale=scale,
            size=size,
        )
