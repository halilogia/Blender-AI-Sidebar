"""Capture active 3D Viewport screenshot semantic tool."""

from typing import Any
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class CaptureViewportTool(BaseTool):
    """Captures the current rendering of the active 3D Viewport."""

    name = "capture_viewport"
    description = (
        "Capture the current rendering of the active 3D Viewport in Blender. "
        "Returns machine-readable image metadata (width, height, format, byte size) "
        "and an in-memory image reference without writing files to disk."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "width": {
                "type": "integer",
                "description": "Desired image width in pixels (default 512, range 64-2048).",
                "default": 512,
                "minimum": 64,
                "maximum": 2048,
            },
            "height": {
                "type": "integer",
                "description": "Desired image height in pixels (default 512, range 64-2048).",
                "default": 512,
                "minimum": 64,
                "maximum": 2048,
            },
        },
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute viewport capture via the adapter."""
        width = kwargs.get("width", 512)
        height = kwargs.get("height", 512)
        return adapter.capture_viewport(width=width, height=height)
