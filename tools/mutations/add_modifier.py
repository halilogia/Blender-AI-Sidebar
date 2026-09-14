"""Add or update a non-destructive geometry modifier on a mesh object in Blender."""

from typing import Any, Dict
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class AddModifierTool(BaseTool):
    """Adds or updates a modifier (BEVEL, SUBSURF, or BOOLEAN) on a target mesh object."""

    name = "add_modifier"
    description = (
        "Add or update a non-destructive modifier on a target mesh object in the Blender scene. "
        "Supports BEVEL (width, segments), SUBSURF (levels), and BOOLEAN (operation, target_object). "
        "Every change records an atomic undo point."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the target mesh object.",
            },
            "modifier_type": {
                "type": "string",
                "enum": ["BEVEL", "SUBSURF", "BOOLEAN"],
                "description": "Type of modifier to add: 'BEVEL', 'SUBSURF', or 'BOOLEAN'.",
            },
            "modifier_name": {
                "type": "string",
                "description": "Optional name for the modifier in the stack. Defaults to the modifier type name.",
            },
            "width": {
                "type": "number",
                "description": "For BEVEL: Bevel width in meters. Default is 0.05.",
            },
            "segments": {
                "type": "integer",
                "description": "For BEVEL: Number of segments for the bevel profile (>= 1). Default is 2.",
            },
            "levels": {
                "type": "integer",
                "description": "For SUBSURF: Viewport subdivision levels (>= 0). Default is 1.",
            },
            "operation": {
                "type": "string",
                "enum": ["DIFFERENCE", "UNION"],
                "description": "For BOOLEAN: Boolean operation mode: 'DIFFERENCE' or 'UNION'. Default is 'DIFFERENCE'.",
            },
            "target_object": {
                "type": "string",
                "description": "For BOOLEAN: Name of the target mesh object to operate with.",
            },
        },
        "required": ["name", "modifier_type"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.LOW

    def get_risk_level(self, arguments: Dict[str, Any]) -> RiskLevel:
        """Determine risk level dynamically based on modifier type."""
        args = arguments or {}
        mod_type = str(args.get("modifier_type", "")).strip().upper()
        if mod_type == "BOOLEAN":
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute add_modifier via the adapter."""
        return adapter.add_modifier(
            name=kwargs.get("name"),
            modifier_type=kwargs.get("modifier_type"),
            modifier_name=kwargs.get("modifier_name"),
            width=kwargs.get("width"),
            segments=kwargs.get("segments"),
            levels=kwargs.get("levels"),
            operation=kwargs.get("operation"),
            target_object=kwargs.get("target_object"),
        )
