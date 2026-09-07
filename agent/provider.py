"""Abstract base class for AI providers.

Zero Blender dependencies. Pure Python.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from core.types import ToolResult
from agent.models import ProviderResponse


class BaseProvider(ABC):
    """Abstract interface for AI model providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        tool_results: Optional[List[ToolResult]] = None,
    ) -> ProviderResponse:
        """Generate response or tool calls based on prompt and optional previous tool results.

        Args:
            prompt: The user prompt.
            tool_results: Results from executed tools in the current turn.

        Returns:
            ProviderResponse containing assistant text or tool calls.
        """
        pass
