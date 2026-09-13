"""Delete an object from the Blender scene by exact name."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class DeleteObjectTool(BaseTool):
    """Safely removes an object from the active Blender scene."""

    name = "delete_object"
    description = (
        "Safely delete and unlink a Blender object from the scene by its exact name. "
        "Records an atomic undo point allowing the object and its state to be restored."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The exact name of the object in the scene to delete.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.MEDIUM

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute object deletion via the adapter."""
        name = kwargs.get("name")
        return adapter.delete_object(name=name)
