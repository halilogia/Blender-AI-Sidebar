"""Accumulator for streaming partial ToolCallDelta fragments into validated ToolCall instances.

Reassembles fragmented tool calls across arbitrary chunk splits, parses arguments JSON,
and validates structural requirements (non-empty ID, tool name, and dict arguments).
Zero Blender dependencies. Zero network dependencies. Pure Python.
"""

import json
from typing import Any, Dict, List, Optional

from agent.models import ToolCall, ToolCallDelta


class ToolCallAccumulatorError(Exception):
    """Raised when accumulator encounters unrecoverable malformed tool call fragments."""

    def __init__(self, code: str, message: str, index: int = 0):
        super().__init__(f"[{code}] {message} (index={index})")
        self.code = code
        self.message = message
        self.index = index


class _AccumulatedCall:
    """Internal state container for a single tool call index."""

    def __init__(self, index: int):
        self.index: int = index
        self.call_id: str = ""
        self.tool_name: str = ""
        self.arguments_buffer: str = ""


class ToolCallAccumulator:
    """Reassembles streaming ToolCallDelta fragments into validated ToolCall objects."""

    def __init__(self):
        self._calls: Dict[int, _AccumulatedCall] = {}

    def feed_delta(self, delta: ToolCallDelta) -> None:
        """Feed a single ToolCallDelta fragment into the accumulator.

        Args:
            delta: ToolCallDelta containing partial or complete fields for an index.
        """
        if delta.index not in self._calls:
            self._calls[delta.index] = _AccumulatedCall(delta.index)

        entry = self._calls[delta.index]

        # 1. Reassemble call_id
        if delta.call_id:
            if not entry.call_id:
                entry.call_id = delta.call_id
            elif entry.call_id != delta.call_id:
                # If subsequent delta sends continuation of call_id
                entry.call_id += delta.call_id

        # 2. Reassemble tool_name
        if delta.tool_name_delta:
            entry.tool_name += delta.tool_name_delta

        # 3. Reassemble arguments JSON buffer
        if delta.arguments_delta:
            entry.arguments_buffer += delta.arguments_delta

    def finalize(self) -> List[ToolCall]:
        """Finalize all accumulated calls and return validated ToolCall instances.

        Returns:
            List of validated ToolCall objects sorted by index.

        Raises:
            ToolCallAccumulatorError: If any call is missing call_id or tool_name,
                                     contains invalid JSON, or arguments parse to a non-dict.
        """
        results: List[ToolCall] = []

        for idx in sorted(self._calls.keys()):
            entry = self._calls[idx]

            # Validation: Call ID
            if not entry.call_id or not entry.call_id.strip():
                raise ToolCallAccumulatorError(
                    code="MISSING_CALL_ID",
                    message=f"Tool call at index {idx} is missing 'call_id'.",
                    index=idx,
                )

            # Validation: Tool Name
            if not entry.tool_name or not entry.tool_name.strip():
                raise ToolCallAccumulatorError(
                    code="MISSING_TOOL_NAME",
                    message=f"Tool call at index {idx} is missing 'tool_name'.",
                    index=idx,
                )

            # Validation & Parsing: Arguments
            raw_args = entry.arguments_buffer.strip()
            if not raw_args:
                parsed_args: Dict[str, Any] = {}
            else:
                try:
                    loaded = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    raise ToolCallAccumulatorError(
                        code="MALFORMED_JSON",
                        message=f"Malformed arguments JSON in tool call '{entry.tool_name}': {exc}",
                        index=idx,
                    ) from exc

                if not isinstance(loaded, dict):
                    raise ToolCallAccumulatorError(
                        code="NON_OBJECT_ARGUMENTS",
                        message=(
                            f"Arguments for tool '{entry.tool_name}' must be a JSON object (dict), "
                            f"got {type(loaded).__name__}."
                        ),
                        index=idx,
                    )
                parsed_args = loaded

            results.append(
                ToolCall(
                    call_id=entry.call_id,
                    tool_name=entry.tool_name,
                    arguments=parsed_args,
                )
            )

        return results

    def reset(self) -> None:
        """Clear all accumulated tool calls."""
        self._calls.clear()
