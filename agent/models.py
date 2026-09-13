"""Data models for chat messages, tool calls, conversation, and provider stream events.

Zero Blender dependencies. Pure Python.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Tuple
from core.types import ToolResult


class Role(str, Enum):
    """Canonical message roles in agent conversations."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ToolCall:
    """Represents an intent to invoke a specific tool with arguments.

    Immutable internal model. Arguments must be a dict.
    """

    call_id: str
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("ToolCall 'call_id' must be a non-empty string.")
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("ToolCall 'tool_name' must be a non-empty string.")
        if not isinstance(self.arguments, dict):
            raise TypeError(f"ToolCall 'arguments' must be a dict, got {type(self.arguments).__name__}.")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize tool call to a deterministic dictionary."""
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": dict(sorted(self.arguments.items())),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        """Deserialize tool call from dictionary."""
        return cls(
            call_id=data.get("call_id", ""),
            tool_name=data.get("tool_name", ""),
            arguments=data.get("arguments", {}),
        )


@dataclass(frozen=True)
class ChatMessage:
    """Internal chat message representation for conversation turns.

    Enforces role-based structural validation:
    - SYSTEM: content allowed, no tool_calls, no tool_call_id
    - USER: content allowed, no tool_calls, no tool_call_id
    - ASSISTANT: content and/or tool_calls allowed, no tool_call_id
    - TOOL: content required, tool_call_id required, name optional, no tool_calls
    """

    role: Role
    content: Optional[str] = None
    tool_calls: Optional[Tuple[ToolCall, ...]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def __init__(
        self,
        role: Any,
        content: Optional[str] = None,
        tool_calls: Optional[List[ToolCall]] = None,
        tool_call_id: Optional[str] = None,
        name: Optional[str] = None,
    ):
        # 1. Role validation and coercion
        if isinstance(role, str):
            try:
                coerced_role = Role(role)
            except ValueError:
                raise ValueError(f"Invalid role: '{role}'. Must be one of {[r.value for r in Role]}.")
        elif isinstance(role, Role):
            coerced_role = role
        else:
            raise TypeError(f"Role must be a Role enum or valid string, got {type(role).__name__}.")

        object.__setattr__(self, "role", coerced_role)

        # 2. Content validation
        if content is not None and not isinstance(content, str):
            raise TypeError(f"ChatMessage 'content' must be a string or None, got {type(content).__name__}.")
        object.__setattr__(self, "content", content)

        # 3. Tool calls validation & immutability coercion
        if tool_calls is not None:
            if not isinstance(tool_calls, (list, tuple)):
                raise TypeError(f"tool_calls must be a list or tuple, got {type(tool_calls).__name__}.")
            for tc in tool_calls:
                if not isinstance(tc, ToolCall):
                    raise TypeError(f"All elements in tool_calls must be ToolCall instances, got {type(tc).__name__}.")
            object.__setattr__(self, "tool_calls", tuple(tool_calls))
        else:
            object.__setattr__(self, "tool_calls", None)

        # 4. Tool call id validation
        if tool_call_id is not None and not isinstance(tool_call_id, str):
            raise TypeError(f"tool_call_id must be a string or None, got {type(tool_call_id).__name__}.")
        object.__setattr__(self, "tool_call_id", tool_call_id)

        # 5. Name validation
        if name is not None and not isinstance(name, str):
            raise TypeError(f"name must be a string or None, got {type(name).__name__}.")
        object.__setattr__(self, "name", name)

        # 6. Role-specific constraints
        if self.role == Role.SYSTEM:
            if self.tool_calls:
                raise ValueError("SYSTEM message cannot contain tool_calls.")
            if self.tool_call_id is not None:
                raise ValueError("SYSTEM message cannot contain tool_call_id.")

        elif self.role == Role.USER:
            if self.tool_calls:
                raise ValueError("USER message cannot contain tool_calls.")
            if self.tool_call_id is not None:
                raise ValueError("USER message cannot contain tool_call_id.")

        elif self.role == Role.ASSISTANT:
            if self.tool_call_id is not None:
                raise ValueError("ASSISTANT message cannot contain tool_call_id.")
            if self.content is None and not self.tool_calls:
                raise ValueError("ASSISTANT message must contain at least 'content' or 'tool_calls'.")

        elif self.role == Role.TOOL:
            if self.content is None:
                raise ValueError("TOOL message must contain 'content'.")
            if not self.tool_call_id or not self.tool_call_id.strip():
                raise ValueError("TOOL message requires a non-empty 'tool_call_id'.")
            if self.tool_calls:
                raise ValueError("TOOL message cannot contain 'tool_calls'.")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize message to dictionary."""
        d: Dict[str, Any] = {"role": self.role.value}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls is not None:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        """Deserialize message from dictionary."""
        tool_calls = None
        if "tool_calls" in data and data["tool_calls"] is not None:
            tool_calls = [ToolCall.from_dict(tc) for tc in data["tool_calls"]]
        return cls(
            role=data["role"],
            content=data.get("content"),
            tool_calls=tool_calls,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
        )


class Conversation:
    """Ordered sequence of ChatMessages forming an agent conversation.

    Pure Python container. Manages message history and sequence validation.
    """

    def __init__(self, messages: Optional[List[ChatMessage]] = None):
        self._messages: List[ChatMessage] = []
        if messages:
            for msg in messages:
                self.add_message(msg)

    @property
    def messages(self) -> List[ChatMessage]:
        """Return a shallow copy of messages list."""
        return list(self._messages)

    def add_message(self, message: ChatMessage) -> None:
        """Append a message to the conversation."""
        if not isinstance(message, ChatMessage):
            raise TypeError(f"Expected ChatMessage, got {type(message).__name__}.")
        self._messages.append(message)

    def clear(self) -> None:
        """Clear all messages from the conversation."""
        self._messages.clear()

    def last(self) -> Optional[ChatMessage]:
        """Return the most recent message, or None if empty."""
        return self._messages[-1] if self._messages else None

    def find_tool_call(self, call_id: str) -> Optional[ToolCall]:
        """Look up a tool call by its call_id across all messages."""
        for msg in reversed(self._messages):
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.call_id == call_id:
                        return tc
        return None

    def validate_sequence(self) -> None:
        """Validate conversation message sequence and tool calling round-trip integrity.

        Enforces:
        - TOOL messages must match a call_id requested in an immediately preceding ASSISTANT message.
        - All requested tool calls must have matching TOOL responses before subsequent non-tool turns.
        """
        pending_tool_call_ids: Dict[str, ToolCall] = {}

        for idx, msg in enumerate(self._messages):
            if msg.role == Role.ASSISTANT:
                if pending_tool_call_ids:
                    unresolved = list(pending_tool_call_ids.keys())
                    raise ValueError(
                        f"Message at index {idx} (ASSISTANT) appeared while tool calls were still pending: {unresolved}."
                    )
                if msg.tool_calls:
                    seen = set()
                    for tc in msg.tool_calls:
                        if tc.call_id in seen:
                            raise ValueError(f"Duplicate tool call_id '{tc.call_id}' in ASSISTANT message at index {idx}.")
                        seen.add(tc.call_id)
                        pending_tool_call_ids[tc.call_id] = tc

            elif msg.role == Role.TOOL:
                if not pending_tool_call_ids:
                    raise ValueError(
                        f"Message at index {idx} (TOOL) with call_id '{msg.tool_call_id}' has no matching preceding ASSISTANT tool call."
                    )
                if msg.tool_call_id not in pending_tool_call_ids:
                    raise ValueError(
                        f"Message at index {idx} (TOOL) with call_id '{msg.tool_call_id}' does not match any pending tool call. Pending: {list(pending_tool_call_ids.keys())}."
                    )
                del pending_tool_call_ids[msg.tool_call_id]

            elif msg.role in (Role.USER, Role.SYSTEM):
                if pending_tool_call_ids:
                    unresolved = list(pending_tool_call_ids.keys())
                    raise ValueError(
                        f"Message at index {idx} ({msg.role.value}) appeared while tool calls were still pending: {unresolved}."
                    )

        if pending_tool_call_ids:
            unresolved = list(pending_tool_call_ids.keys())
            raise ValueError(f"Conversation has unresolved pending tool calls: {unresolved}.")

    def to_list(self) -> List[Dict[str, Any]]:
        """Serialize conversation messages to a list of dictionaries."""
        return [msg.to_dict() for msg in self._messages]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize conversation to a dictionary."""
        return {"messages": self.to_list()}

    @classmethod
    def from_list(cls, messages_data: List[Dict[str, Any]]) -> "Conversation":
        """Deserialize conversation from a list of message dicts."""
        conv = cls()
        for md in messages_data:
            conv.add_message(ChatMessage.from_dict(md))
        return conv

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Conversation":
        """Deserialize conversation from a dictionary."""
        return cls.from_list(data.get("messages", []))

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    def __getitem__(self, index: int) -> ChatMessage:
        return self._messages[index]


# ==============================================================================
# PROVIDER STREAM EVENT MODELS (Provider Stream Protocol != Agent Runtime Events)
# ==============================================================================

@dataclass(frozen=True)
class ProviderStreamEvent:
    """Base class for provider stream events."""

    turn_id: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to dictionary."""
        raise NotImplementedError


@dataclass(frozen=True)
class TextDelta(ProviderStreamEvent):
    """Streaming text token chunk from provider."""

    turn_id: str
    text: str

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise TypeError(f"TextDelta 'text' must be a str, got {type(self.text).__name__}.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": "text_delta",
            "turn_id": self.turn_id,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TextDelta":
        return cls(turn_id=data["turn_id"], text=data["text"])


@dataclass(frozen=True)
class ToolCallDelta(ProviderStreamEvent):
    """Streaming partial tool call fragment.

    NOT an executable ToolCall. Normalized accumulation occurs later.
    """

    turn_id: str
    index: int
    call_id: Optional[str] = None
    tool_name_delta: Optional[str] = None
    arguments_delta: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.index, int) or self.index < 0:
            raise ValueError(f"ToolCallDelta 'index' must be a non-negative int, got {self.index}.")
        if self.call_id is not None and not isinstance(self.call_id, str):
            raise TypeError(f"ToolCallDelta 'call_id' must be a str or None, got {type(self.call_id).__name__}.")
        if self.tool_name_delta is not None and not isinstance(self.tool_name_delta, str):
            raise TypeError(f"ToolCallDelta 'tool_name_delta' must be a str or None, got {type(self.tool_name_delta).__name__}.")
        if self.arguments_delta is not None and not isinstance(self.arguments_delta, str):
            raise TypeError(f"ToolCallDelta 'arguments_delta' must be a str or None, got {type(self.arguments_delta).__name__}.")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "event": "tool_call_delta",
            "turn_id": self.turn_id,
            "index": self.index,
        }
        if self.call_id is not None:
            d["call_id"] = self.call_id
        if self.tool_name_delta is not None:
            d["tool_name_delta"] = self.tool_name_delta
        if self.arguments_delta is not None:
            d["arguments_delta"] = self.arguments_delta
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCallDelta":
        return cls(
            turn_id=data["turn_id"],
            index=data["index"],
            call_id=data.get("call_id"),
            tool_name_delta=data.get("tool_name_delta"),
            arguments_delta=data.get("arguments_delta"),
        )


@dataclass(frozen=True)
class ProviderCompleted(ProviderStreamEvent):
    """Indicates provider has completed stream generation."""

    turn_id: str
    finish_reason: str
    usage: Optional[Dict[str, int]] = None

    def __post_init__(self):
        if not isinstance(self.finish_reason, str):
            raise TypeError(f"ProviderCompleted 'finish_reason' must be str, got {type(self.finish_reason).__name__}.")
        if self.usage is not None and not isinstance(self.usage, dict):
            raise TypeError(f"ProviderCompleted 'usage' must be a dict or None, got {type(self.usage).__name__}.")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "event": "provider_completed",
            "turn_id": self.turn_id,
            "finish_reason": self.finish_reason,
        }
        if self.usage is not None:
            d["usage"] = dict(sorted(self.usage.items()))
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderCompleted":
        return cls(
            turn_id=data["turn_id"],
            finish_reason=data["finish_reason"],
            usage=data.get("usage"),
        )


class ProviderErrorType(str, Enum):
    """Standardized error categories for LLM/provider failures (distinct from ToolError)."""

    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    TOOL_CALL_PARSE_ERROR = "TOOL_CALL_PARSE_ERROR"
    CANCELLED = "CANCELLED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


@dataclass(frozen=True)
class ProviderError(ProviderStreamEvent):
    """Provider-level failure (distinct from ToolError)."""

    turn_id: str
    type: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if not isinstance(self.type, (str, ProviderErrorType)):
            raise TypeError(f"ProviderError 'type' must be str or ProviderErrorType, got {type(self.type).__name__}.")
        if not isinstance(self.message, str):
            raise TypeError(f"ProviderError 'message' must be str, got {type(self.message).__name__}.")
        if self.details is not None and not isinstance(self.details, dict):
            raise TypeError(f"ProviderError 'details' must be a dict or None, got {type(self.details).__name__}.")

    def to_dict(self) -> Dict[str, Any]:
        type_val = self.type.value if isinstance(self.type, ProviderErrorType) else str(self.type)
        d: Dict[str, Any] = {
            "event": "provider_error",
            "turn_id": self.turn_id,
            "type": type_val,
            "message": self.message,
        }
        if self.details is not None:
            d["details"] = dict(sorted(self.details.items()))
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderError":
        return cls(
            turn_id=data["turn_id"],
            type=data["type"],
            message=data["message"],
            details=data.get("details"),
        )


# ==============================================================================
# M1 PROVIDER / RUNTIME DATA MODELS (Maintained for Backward Compatibility)
# ==============================================================================

@dataclass(frozen=True)
class ProviderResponse:
    """Output returned by an AI provider (Mock or real LLM)."""

    assistant_text: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    is_final: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize provider response to a dictionary."""
        return {
            "assistant_text": self.assistant_text,
            "is_final": self.is_final,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
        }


@dataclass(frozen=True)
class AgentResult:
    """Final output of an agent runtime execution turn."""

    final_text: str
    tool_results: List[ToolResult] = field(default_factory=list)
    state: str = "IDLE"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize agent result to a dictionary."""
        return {
            "final_text": self.final_text,
            "state": self.state,
            "tool_results": [tr.to_dict() for tr in self.tool_results],
        }

