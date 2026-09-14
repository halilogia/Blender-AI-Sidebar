"""Create or modify a light object in the active Blender scene."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class CreateLightTool(BaseTool):
    """Creates a new light or modifies an existing light in the scene."""

    name = "create_light"
    description = (
        "Create a new light or modify an existing light in the Blender scene "
        "with specified light type (POINT, SUN, SPOT, AREA), position, orientation, energy, and color. "
        "Every change records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Optional name for the light. If a light with this name already exists, it will be modified. Defaults to 'Light'.",
            },
            "light_type": {
                "type": "string",
                "enum": ["POINT", "SUN", "SPOT", "AREA"],
                "description": "Type of light source: POINT, SUN, SPOT, or AREA. Default is 'POINT'.",
            },
            "location": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] world position coordinates in meters.",
            },
            "rotation": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Target [X, Y, Z] Euler rotation angles in radians.",
            },
            "energy": {
                "type": "number",
                "description": "Light energy / power (watts for POINT/SPOT/AREA, strength for SUN). Must be non-negative. Default is 10.0.",
            },
            "color": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 3,
                "maxItems": 3,
                "description": "Light RGB color values as [R, G, B], each between 0.0 and 1.0. Default is [1.0, 1.0, 1.0].",
            },
        },
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute light creation or modification via the adapter."""
        return adapter.create_light(
            name=kwargs.get("name"),
            light_type=kwargs.get("light_type"),
            location=kwargs.get("location"),
            rotation=kwargs.get("rotation"),
            energy=kwargs.get("energy"),
            color=kwargs.get("color"),
        )
