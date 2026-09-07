"""Inspect viewport selection state and active mode."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class InspectSelectionTool(BaseTool):
    """Inspects current selection and interaction mode."""

    name = "inspect_selection"
    description = (
        "Inspect the current viewport selection state, active object, "
        "selected objects count, and active interaction mode."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute selection inspection via the adapter."""
        return adapter.inspect_selection()
