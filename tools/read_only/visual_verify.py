"""Visual verification semantic tool for active 3D Viewport."""

from typing import Any, Dict, Optional
from core.types import RiskLevel, ToolResult
from tools.base import BaseTool


class VisualVerifyTool(BaseTool):
    """Inspect and verify the active 3D Viewport against an expected visual description."""

    name = "visual_verify"
    description = (
        "Inspect and verify the active 3D Viewport against an expected visual description. "
        "Captures the viewport screenshot and initiates visual verification."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "expected_description": {
                "type": "string",
                "description": "Concise natural language description of what should be visually verified in the scene.",
            },
            "image_id": {
                "type": "string",
                "description": "Optional image_id of an existing viewport capture. If omitted, captures the active 3D Viewport.",
            },
            "width": {
                "type": "integer",
                "description": "Image width in pixels if new capture needed (default 512, range 64-2048).",
                "default": 512,
                "minimum": 64,
                "maximum": 2048,
            },
            "height": {
                "type": "integer",
                "description": "Image height in pixels if new capture needed (default 512, range 64-2048).",
                "default": 512,
                "minimum": 64,
                "maximum": 2048,
            },
        },
        "required": ["expected_description"],
        "additionalProperties": False,
    }
    risk_level = RiskLevel.READ_ONLY

    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute viewport capture for visual verification via adapter."""
        expected_description = kwargs.get("expected_description", "")
        if not isinstance(expected_description, str) or not expected_description.strip():
            return ToolResult.fail(
                tool=self.name,
                error_type="INVALID_ARGUMENTS",
                message="expected_description is required and cannot be empty.",
            )

        desc = expected_description.strip()
        image_id = kwargs.get("image_id")

        if not image_id:
            width = kwargs.get("width", 512)
            height = kwargs.get("height", 512)
            cap_res = adapter.capture_viewport(width=width, height=height)
            if not cap_res.success:
                return cap_res
            image_id = cap_res.data.get("image_id")

        return ToolResult.ok(
            tool=self.name,
            data={
                "image_id": image_id,
                "expected_description": desc,
                "status": "CAPTURED",
            },
        )
