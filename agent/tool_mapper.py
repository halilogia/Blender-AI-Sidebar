"""OpenAI-compatible function tool schema mapper.

Converts internal tool schemas from ToolRegistry / BaseTool into
OpenAI Chat Completions function tool definitions.
Zero Blender (bpy) dependencies. Pure Python.
"""

from typing import Any, Dict, Iterable, List, Union
from tools.base import BaseTool


class ToolMappingError(ValueError):
    """Raised when an internal tool schema cannot be mapped to OpenAI format."""

    pass


class OpenAICompatibleToolMapper:
    """Maps internal tool definitions to OpenAI Chat Completions function tool schemas."""

    @classmethod
    def map_tool(cls, tool_or_schema: Union[BaseTool, Dict[str, Any]]) -> Dict[str, Any]:
        """Convert a single tool instance or schema dict into an OpenAI function tool dict.

        Format:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": { ... },
                    "required": [ ... ]
                }
            }
        }

        Raises:
            ToolMappingError: If the tool definition violates contract requirements.
        """
        # 1. Extract name, description, and input_schema
        if isinstance(tool_or_schema, BaseTool):
            name = getattr(tool_or_schema, "name", None)
            description = getattr(tool_or_schema, "description", None)
            input_schema = getattr(tool_or_schema, "input_schema", None)
        elif isinstance(tool_or_schema, dict):
            name = tool_or_schema.get("name")
            description = tool_or_schema.get("description")
            # Can be under 'input_schema' or 'parameters'
            input_schema = tool_or_schema.get("input_schema")
            if input_schema is None and "parameters" in tool_or_schema:
                input_schema = tool_or_schema.get("parameters")
        else:
            raise ToolMappingError(
                f"Expected BaseTool instance or dict, got {type(tool_or_schema).__name__}."
            )

        # 2. Validate name
        if not isinstance(name, str) or not name.strip():
            raise ToolMappingError("Tool schema is missing a valid non-empty 'name'.")

        # 3. Validate description
        if not isinstance(description, str) or not description.strip():
            raise ToolMappingError(f"Tool '{name}' is missing a valid non-empty 'description'.")

        # 4. Validate input_schema
        if input_schema is None or not isinstance(input_schema, dict):
            raise ToolMappingError(f"Tool '{name}' must provide a dict 'input_schema'.")

        # 5. Validate JSON-schema structure
        schema_type = input_schema.get("type")
        if schema_type is not None and schema_type != "object":
            raise ToolMappingError(
                f"Tool '{name}' input_schema 'type' must be 'object', got '{schema_type}'."
            )

        properties = input_schema.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ToolMappingError(
                f"Tool '{name}' input_schema 'properties' must be a dict, got {type(properties).__name__}."
            )

        required = input_schema.get("required")
        if required is not None and not isinstance(required, (list, tuple)):
            raise ToolMappingError(
                f"Tool '{name}' input_schema 'required' must be a list or tuple, got {type(required).__name__}."
            )

        # 6. Build deterministic OpenAI-compatible function schema
        parameters: Dict[str, Any] = {
            "type": "object",
        }
        if properties is not None:
            # Deterministically sort properties
            parameters["properties"] = dict(sorted(properties.items()))
        else:
            parameters["properties"] = {}

        if required is not None:
            parameters["required"] = sorted(list(required))

        if "additionalProperties" in input_schema:
            parameters["additionalProperties"] = input_schema["additionalProperties"]

        return {
            "type": "function",
            "function": {
                "name": name.strip(),
                "description": description.strip(),
                "parameters": parameters,
            },
        }

    @classmethod
    def map_tools(
        cls, tools_or_schemas: Iterable[Union[BaseTool, Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """Map multiple tools or schemas to OpenAI format, preserving input ordering."""
        return [cls.map_tool(item) for item in tools_or_schemas]
