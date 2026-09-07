"""Inspect deep properties of a single object by name."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class InspectObjectTool(BaseTool):
    """Inspects detailed properties of an individual object."""

    name = "inspect_object"
    description = (
        "Inspect deep properties of a Blender object by name, including transforms, "
        "dimensions, collections, assigned materials, and modifiers."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The unique name of the target object to inspect.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute deep object inspection via the adapter."""
        name = kwargs.get("name")
        return adapter.inspect_object(name=name)
