"""Assign an existing or new material to a target object slot."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class AssignMaterialTool(BaseTool):
    """Assigns a material by name to an object at a specified slot index."""

    name = "assign_material"
    description = (
        "Assign a material by name to a target object at an optional slot index. "
        "Creates the material with Principled BSDF if it does not already exist. "
        "Every assignment records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": "Name of the target object to assign the material to.",
            },
            "material_name": {
                "type": "string",
                "description": "Name of the material to assign.",
            },
            "slot_index": {
                "type": "integer",
                "default": 0,
                "description": "Zero-indexed material slot on the object. Defaults to 0.",
            },
        },
        "required": ["object_name", "material_name"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute material assignment via adapter."""
        object_name = kwargs.get("object_name")
        material_name = kwargs.get("material_name")
        slot_index = kwargs.get("slot_index", 0)

        return adapter.assign_material(
            object_name=object_name,
            material_name=material_name,
            slot_index=slot_index,
        )
