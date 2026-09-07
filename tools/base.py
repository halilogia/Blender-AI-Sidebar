"""BaseTool definition and contract validation.

Every tool in the system inherits from BaseTool and adheres to a strict contract.
Zero Blender dependencies.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from core.types import RiskLevel, ToolResult


class InvalidToolContractError(Exception):
    """Raised when a tool definition violates the required contract."""


class BaseTool(ABC):
    """Abstract base class for all agent tools."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    risk_level: RiskLevel = RiskLevel.READ_ONLY

    def __init_subclass__(cls, **kwargs):
        """Validate tool subclass definitions at registration time."""
        super().__init_subclass__(**kwargs)
        # Only validate concrete (non-abstract) tool classes
        if not getattr(cls, "__abstractmethods__", None):
            cls._validate_class_contract()

    @classmethod
    def _validate_class_contract(cls) -> None:
        """Enforce non-empty name, description, schema, and valid risk level."""
        if not hasattr(cls, "name") or not isinstance(cls.name, str) or not cls.name.strip():
            raise InvalidToolContractError(f"Tool {cls.__name__} must define a non-empty string 'name'.")

        if not hasattr(cls, "description") or not isinstance(cls.description, str) or not cls.description.strip():
            raise InvalidToolContractError(f"Tool {cls.__name__} must define a non-empty string 'description'.")

        if not hasattr(cls, "input_schema") or not isinstance(cls.input_schema, dict):
            raise InvalidToolContractError(f"Tool {cls.__name__} must define a dict 'input_schema'.")

        if not hasattr(cls, "risk_level") or not isinstance(cls.risk_level, RiskLevel):
            raise InvalidToolContractError(
                f"Tool {cls.__name__} must define a valid RiskLevel enum member (got {getattr(cls, 'risk_level', None)})."
            )

    @abstractmethod
    def execute(self, adapter: Any, **kwargs) -> ToolResult:
        """Execute the tool logic using the provided adapter and keyword arguments.

        Args:
            adapter: The BlenderAdapter instance (or mock in tests).
            **kwargs: Arguments conforming to self.input_schema.

        Returns:
            ToolResult: The standardized execution result.
        """
        pass

    def to_schema(self) -> Dict[str, Any]:
        """Export tool declaration to a deterministic dictionary for AI models."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "risk_level": self.risk_level.value,
        }
