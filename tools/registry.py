"""ToolRegistry for managing and discovering agent tools.

Provides registration, lookup, validation, and schema export with deterministic ordering.
Zero Blender dependencies.
"""

from typing import Dict, List, Optional

from tools.base import BaseTool


class ToolAlreadyRegisteredError(Exception):
    """Raised when attempting to register a tool with an already existing name."""


class ToolNotFoundError(Exception):
    """Raised when attempting to access an unregistered tool."""


class ToolRegistry:
    """In-memory registry of available agent tools."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance.

        Raises:
            ToolAlreadyRegisteredError: If a tool with the same name is already registered.
            TypeError: If the object does not inherit from BaseTool.
        """
        if not isinstance(tool, BaseTool):
            raise TypeError(f"Expected BaseTool instance, got {type(tool).__name__}")

        name = tool.name
        if name in self._tools:
            raise ToolAlreadyRegisteredError(f"Tool '{name}' is already registered in ToolRegistry.")

        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name.

        Raises:
            ToolNotFoundError: If the tool name is not registered.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Cannot unregister: Tool '{name}' is not registered.")
        del self._tools[name]

    def get(self, name: str) -> BaseTool:
        """Retrieve a registered tool by name.

        Raises:
            ToolNotFoundError: If the tool name is not registered.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    def exists(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def list(self) -> List[BaseTool]:
        """Return all registered tools in deterministic alphabetical order by name."""
        return [self._tools[name] for name in sorted(self._tools.keys())]

    def export_schemas(self) -> List[Dict[str, any]]:
        """Export all tool schemas in deterministic alphabetical order by name."""
        return [tool.to_schema() for tool in self.list()]

    def clear(self) -> None:
        """Remove all registered tools."""
        self._tools.clear()

    def __len__(self) -> int:
        return len(self._tools)
