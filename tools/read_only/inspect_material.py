"""Inspect material properties, Principled BSDF parameters, and node summary."""

from typing import Any, Optional
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class InspectMaterialTool(BaseTool):
    """Inspects material shader nodes and slot bindings with dual-entry contract."""

    name = "inspect_material"
    description = (
        "Inspect material shader nodes and Principled BSDF settings. "
        "Accepts either 'material_name' directly OR 'object_name' with an optional 'slot_index'."
    )
    input_schema = {
        "type": "object",
        "properties": {
          "material_name": {
            "type": "string",
            "description": "Direct name of the material in Blender datablocks.",
          },
          "object_name": {
            "type": "string",
            "description": "Name of the object holding the target material slot.",
          },
          "slot_index": {
            "type": "integer",
            "description": "Material slot index on the object (zero-indexed, default 0).",
            "default": 0,
          },
        },
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute material inspection via adapter."""
        material_name: Optional[str] = kwargs.get("material_name")
        object_name: Optional[str] = kwargs.get("object_name")
        slot_index: Optional[int] = kwargs.get("slot_index", 0)

        # Validate dual-entry contract
        if material_name is not None and object_name is not None:
            return ToolResult.fail(
                tool=self.name,
                error_type="INVALID_ARGUMENT",
                message="Provide either 'material_name' or 'object_name', not both.",
                details={"material_name": material_name, "object_name": object_name},
            )

        if material_name is None and object_name is None:
            return ToolResult.fail(
                tool=self.name,
                error_type="INVALID_ARGUMENT",
                message="Must provide either 'material_name' or 'object_name'.",
                details={},
            )

        return adapter.inspect_material(
            material_name=material_name,
            object_name=object_name,
            slot_index=slot_index,
        )
