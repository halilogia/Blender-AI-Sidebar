"""Core domain types for Blender AI Sidebar.

This module defines tool execution results, error models, and risk levels.
It has zero dependencies on Blender and is 100% pure Python.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class RiskLevel(str, Enum):
    """Execution risk categories for tools."""

    READ_ONLY = "READ_ONLY"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ToolError:
    """Structured, deterministic error representation."""

    type: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize error to a deterministic JSON-compatible dictionary."""
        return {
            "type": self.type,
            "message": self.message,
            "details": dict(sorted(self.details.items())) if self.details else {},
        }


@dataclass(frozen=True)
class ToolResult:
    """Standardized tool execution contract."""

    success: bool
    tool: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[ToolError] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize tool result to a deterministic JSON-compatible dictionary."""
        return {
            "success": self.success,
            "tool": self.tool,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
        }

    @classmethod
    def ok(cls, tool: str, data: Dict[str, Any]) -> "ToolResult":
        """Factory for successful tool results."""
        return cls(success=True, tool=tool, data=data, error=None)

    @classmethod
    def fail(
        cls,
        tool: str,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        """Factory for failed tool results with structured error."""
        error = ToolError(
            type=error_type,
            message=message,
            details=details or {},
        )
        return cls(success=False, tool=tool, data=None, error=error)
