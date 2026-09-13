"""Event data models and turn metrics for the asynchronous boundary.

Zero Blender dependencies. Pure Python.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional

from core.types import ToolResult
from agent.models import ProviderResponse, ToolCall


class EventType:
    """Canonical event types traversing the worker/main-thread boundary."""

    PROMPT_SUBMITTED = "PROMPT_SUBMITTED"
    PROVIDER_RESPONSE_READY = "PROVIDER_RESPONSE_READY"
    TOOL_CALL_REQUESTED = "TOOL_CALL_REQUESTED"
    TOOL_RESULT_READY = "TOOL_RESULT_READY"
    FINAL_RESPONSE_READY = "FINAL_RESPONSE_READY"
    AGENT_ERROR = "AGENT_ERROR"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    STREAMING_TEXT_DELTA = "STREAMING_TEXT_DELTA"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class TurnMetrics:
    """Timing and performance metrics for an agent turn."""

    turn_id: str
    t_submitted: float = field(default_factory=time.time)
    t_first_event: Optional[float] = None
    t_tools_duration: float = 0.0
    t_completed: Optional[float] = None

    @property
    def total_duration_sec(self) -> Optional[float]:
        """Calculate total turn duration in seconds if completed."""
        if self.t_completed is not None:
            return round(self.t_completed - self.t_submitted, 4)
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize metrics to a dictionary."""
        return {
            "turn_id": self.turn_id,
            "t_submitted": round(self.t_submitted, 4),
            "t_first_event": round(self.t_first_event, 4) if self.t_first_event else None,
            "t_tools_duration": round(self.t_tools_duration, 4),
            "t_completed": round(self.t_completed, 4) if self.t_completed else None,
            "total_duration_sec": self.total_duration_sec,
        }


@dataclass
class Event:
    """Base event model exchanged over the thread-safe queue."""

    event_type: str
    turn_id: str
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to a deterministic dictionary."""
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": round(self.timestamp, 4),
            "turn_id": self.turn_id,
        }


@dataclass
class PromptSubmittedEvent(Event):
    """User submitted a new prompt."""

    prompt: str = ""

    def __init__(self, prompt: str, turn_id: str, timestamp: Optional[float] = None):
        super().__init__(
            event_type=EventType.PROMPT_SUBMITTED,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload={"prompt": prompt},
        )
        self.prompt = prompt


@dataclass
class ProviderResponseReadyEvent(Event):
    """Provider finished generating a step (assistant text and/or tool calls)."""

    response: Optional[ProviderResponse] = None

    def __init__(self, response: ProviderResponse, turn_id: str, timestamp: Optional[float] = None):
        super().__init__(
            event_type=EventType.PROVIDER_RESPONSE_READY,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload=response.to_dict(),
        )
        self.response = response


@dataclass
class ToolCallRequestedEvent(Event):
    """A tool call needs execution on the main thread."""

    tool_call: Optional[ToolCall] = None

    def __init__(self, tool_call: ToolCall, turn_id: str, timestamp: Optional[float] = None):
        super().__init__(
            event_type=EventType.TOOL_CALL_REQUESTED,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload=tool_call.to_dict(),
        )
        self.tool_call = tool_call


@dataclass
class ToolResultReadyEvent(Event):
    """A tool finished executing on the main thread."""

    tool_result: Optional[ToolResult] = None

    def __init__(self, tool_result: ToolResult, turn_id: str, timestamp: Optional[float] = None):
        super().__init__(
            event_type=EventType.TOOL_RESULT_READY,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload=tool_result.to_dict(),
        )
        self.tool_result = tool_result


@dataclass
class FinalResponseReadyEvent(Event):
    """Turn completed successfully with final assistant text."""

    final_text: str = ""
    tool_results: List[ToolResult] = field(default_factory=list)
    metrics: Optional[TurnMetrics] = None

    def __init__(
        self,
        final_text: str,
        turn_id: str,
        tool_results: Optional[List[ToolResult]] = None,
        metrics: Optional[TurnMetrics] = None,
        timestamp: Optional[float] = None,
    ):
        results = tool_results or []
        payload_data: Dict[str, Any] = {
            "final_text": final_text,
            "tool_results": [tr.to_dict() for tr in results],
            "metrics": metrics.to_dict() if metrics else None,
        }
        super().__init__(
            event_type=EventType.FINAL_RESPONSE_READY,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload=payload_data,
        )
        self.final_text = final_text
        self.tool_results = results
        self.metrics = metrics


@dataclass
class AgentErrorEvent(Event):
    """An unhandled or structured error occurred during worker or main-thread execution."""

    error_type: str = "UNKNOWN_ERROR"
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        error_type: str,
        message: str,
        turn_id: str,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ):
        det = details or {}
        super().__init__(
            event_type=EventType.AGENT_ERROR,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload={
                "details": det,
                "error_type": error_type,
                "message": message,
            },
        )
        self.error_type = error_type
        self.message = message
        self.details = det


@dataclass
class CancelRequestedEvent(Event):
    """Current turn cancellation was requested."""

    def __init__(self, turn_id: str, timestamp: Optional[float] = None):
        super().__init__(
            event_type=EventType.CANCEL_REQUESTED,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload={"action": "cancel"},
        )


@dataclass
class StreamingTextDeltaEvent(Event):
    """Incremental assistant text delta received from provider streaming."""

    delta: str = ""

    def __init__(self, delta: str, turn_id: str, timestamp: Optional[float] = None):
        super().__init__(
            event_type=EventType.STREAMING_TEXT_DELTA,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload={"delta": delta},
        )
        self.delta = delta


@dataclass
class ShutdownEvent(Event):
    """Extension teardown signal."""

    def __init__(self, turn_id: str = "system", timestamp: Optional[float] = None):
        super().__init__(
            event_type=EventType.SHUTDOWN,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload={"action": "shutdown"},
        )


@dataclass
class ApprovalRequiredEvent(Event):
    """A gated tool call requires user confirmation before execution."""

    approval_id: str = ""
    tool_name: str = ""
    risk_level: str = "MEDIUM"
    description: str = ""

    def __init__(
        self,
        approval_id: str,
        tool_name: str,
        risk_level: str,
        description: str,
        turn_id: str,
        timestamp: Optional[float] = None,
    ):
        super().__init__(
            event_type=EventType.APPROVAL_REQUIRED,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload={
                "approval_id": approval_id,
                "description": description,
                "risk_level": risk_level,
                "tool_name": tool_name,
            },
        )
        self.approval_id = approval_id
        self.tool_name = tool_name
        self.risk_level = risk_level
        self.description = description


@dataclass
class ApprovalResolvedEvent(Event):
    """User approved or rejected a gated tool call."""

    approval_id: str = ""
    decision: str = ""
    tool_name: str = ""

    def __init__(
        self,
        approval_id: str,
        decision: str,
        tool_name: str,
        turn_id: str,
        timestamp: Optional[float] = None,
    ):
        super().__init__(
            event_type=EventType.APPROVAL_RESOLVED,
            turn_id=turn_id,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload={
                "approval_id": approval_id,
                "decision": decision,
                "tool_name": tool_name,
            },
        )
        self.approval_id = approval_id
        self.decision = decision
        self.tool_name = tool_name
