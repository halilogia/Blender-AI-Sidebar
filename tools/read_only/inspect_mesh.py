"""Inspect mesh geometry metrics, topology breakdown, and world-space bounding box."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class InspectMeshTool(BaseTool):
    """Inspects aggregate mesh metrics, polygon breakdown, and world bounding box."""

    name = "inspect_mesh"
    description = (
        "Inspect mesh geometry metrics for a target object, including vertex/edge/polygon counts, "
        "triangle/quad/ngon breakdown, UV layers, and world-space bounding box."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": "The name of the target mesh object to inspect.",
            },
        },
        "required": ["object_name"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute mesh inspection via adapter."""
        object_name = kwargs.get("object_name")
        if not object_name or not isinstance(object_name, str):
            return ToolResult.fail(
                tool=self.name,
                error_type="INVALID_ARGUMENT",
                message="Argument 'object_name' must be a non-empty string.",
                details={"provided": str(object_name)},
            )
        return adapter.inspect_mesh(object_name=object_name)
