"""Stateless classification of events handled by :class:`AgentRuntime`.

This module deliberately contains no lifecycle or execution logic.  The
runtime remains the owner of stale-turn filtering, state transitions,
conversation/history updates, queueing, worker submission, and main-thread
tool execution.  The router only preserves the existing type-priority order
and tells the runtime which existing branch should handle an event.
"""

from enum import Enum
from typing import Any

from core.events import (
    AgentErrorEvent,
    ProviderResponseReadyEvent,
    StreamingTextDeltaEvent,
)
from agent.models import ProviderCompleted, ProviderError, TextDelta, ToolCallDelta


class EventRoute(str, Enum):
    """Existing ``AgentRuntime.process_event`` branches."""

    STREAMING_TEXT_DELTA = "streaming_text_delta"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_COMPLETED = "provider_completed"
    PROVIDER_RESPONSE_READY = "provider_response_ready"
    AGENT_ERROR = "agent_error"
    UNHANDLED = "unhandled"


class EventRouter:
    """Stateless event classifier used by ``AgentRuntime``.

    The order intentionally mirrors the legacy ``process_event`` checks.
    ``route`` has no side effects and does not inspect or mutate runtime
    state, so lifecycle behavior remains in ``AgentRuntime``.
    """

    __slots__ = ()

    def route(self, event: Any) -> EventRoute:
        """Return the existing runtime branch for ``event``."""
        if isinstance(event, StreamingTextDeltaEvent):
            return EventRoute.STREAMING_TEXT_DELTA
        if isinstance(event, TextDelta):
            return EventRoute.TEXT_DELTA
        if isinstance(event, ToolCallDelta):
            return EventRoute.TOOL_CALL_DELTA
        if isinstance(event, ProviderError):
            return EventRoute.PROVIDER_ERROR
        if isinstance(event, ProviderCompleted):
            return EventRoute.PROVIDER_COMPLETED
        if isinstance(event, ProviderResponseReadyEvent):
            return EventRoute.PROVIDER_RESPONSE_READY
        if isinstance(event, AgentErrorEvent):
            return EventRoute.AGENT_ERROR
        return EventRoute.UNHANDLED
