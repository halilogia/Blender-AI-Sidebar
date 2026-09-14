"""Create or modify a camera object in the active Blender scene."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class CreateCameraTool(BaseTool):
    """Creates a new camera or modifies an existing camera in the scene."""

    name = "create_camera"
    description = (
        "Create a new camera or modify an existing camera in the Blender scene "
        "with specified position, orientation, focal length (lens), and active scene camera state. "
        "Every change records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Optional name for the camera. If a camera with this name already exists, it will be modified. Defaults to 'Camera'.",
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
            "lens": {
                "type": "number",
                "description": "Camera focal length in millimeters (e.g. 50.0). Must be positive. Default is 50.0.",
            },
            "make_active": {
                "type": "boolean",
                "description": "Whether to set this camera as the active camera for the scene. Default is true.",
            },
        },
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute camera creation or modification via the adapter."""
        return adapter.create_camera(
            name=kwargs.get("name"),
            location=kwargs.get("location"),
            rotation=kwargs.get("rotation"),
            lens=kwargs.get("lens"),
            make_active=kwargs.get("make_active", True),
        )
