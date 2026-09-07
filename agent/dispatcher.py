"""ToolDispatcher for validating arguments and dispatching tool execution.

Zero Blender dependencies. Pure Python.
"""

from typing import Any, Dict
from core.types import ToolResult
from tools.registry import ToolRegistry, ToolNotFoundError
from agent.models import ToolCall


class ToolDispatcher:
    """Dispatches validated tool calls against a ToolRegistry and Adapter."""

    def __init__(self, registry: ToolRegistry, adapter: Any):
        self.registry = registry
        self.adapter = adapter

    def dispatch(self, tool_call: ToolCall) -> ToolResult:
        """Validate and execute a single tool call.

        Args:
            tool_call: The ToolCall instance containing tool_name and arguments.

        Returns:
            ToolResult: The standardized tool execution outcome.
        """
        tool_name = tool_call.tool_name

        # 1. Resolve tool from registry
        if not self.registry.exists(tool_name):
            return ToolResult.fail(
                tool=tool_name,
                error_type="TOOL_NOT_FOUND",
                message=f"Tool '{tool_name}' is not registered in ToolRegistry.",
                details={"queried_tool": tool_name},
            )

        tool = self.registry.get(tool_name)
        schema = tool.input_schema
        arguments = tool_call.arguments or {}

        # 2. Validate input schema: required fields
        required_fields = schema.get("required", [])
        for field in required_fields:
            if field not in arguments:
                return ToolResult.fail(
                    tool=tool_name,
                    error_type="INVALID_ARGUMENT",
                    message=f"Missing required argument '{field}' for tool '{tool_name}'.",
                    details={"missing_field": field, "required": required_fields},
                )

        # 3. Validate additional properties if forbidden
        if schema.get("additionalProperties") is False:
            allowed_properties = set(schema.get("properties", {}).keys())
            unexpected = [arg for arg in arguments if arg not in allowed_properties]
            if unexpected:
                return ToolResult.fail(
                    tool=tool_name,
                    error_type="INVALID_ARGUMENT",
                    message=f"Unexpected argument(s) {unexpected} for tool '{tool_name}'.",
                    details={"unexpected": unexpected, "allowed": list(allowed_properties)},
                )

        # 4. Execute tool through adapter
        try:
            return tool.execute(self.adapter, **arguments)
        except Exception as exc:
            return ToolResult.fail(
                tool=tool_name,
                error_type="DISPATCHER_ERROR",
                message=f"Unhandled exception executing tool '{tool_name}': {str(exc)}",
                details={"exception": type(exc).__name__},
            )
