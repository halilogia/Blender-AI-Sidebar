"""OpenAI-compatible Chat Completions streaming provider.

Maps internal ProviderRequestContext into OpenAI-compatible Chat Completions payload,
dispatches streaming POST requests via HttpClient, parses SSE events with SSEParser,
accumulates streaming tool call fragments via ToolCallAccumulator, and normalizes
events into ProviderStreamEvents (TextDelta, ToolCallDelta, ProviderCompleted, ProviderError).

Zero Blender (bpy) dependencies. Zero tool execution. Pure Python.
"""

from dataclasses import dataclass
import json
import threading
from typing import Any, Dict, Iterator, List, Mapping, Optional, Union

from core.config import Config, is_network_allowed
from agent.context_builder import ProviderRequestContext
from agent.http_client import (
    HttpClient,
    HttpConnectionError,
    HttpError,
    HttpTimeoutError,
    NetworkError,
)
from agent.models import (
    ChatMessage,
    ProviderCompleted,
    ProviderError,
    ProviderErrorType,
    ProviderStreamEvent,
    Role,
    TextDelta,
    ToolCall,
    ToolCallDelta,
)
from agent.sse_parser import SSEParser, SSEParseError
from agent.tool_call_accumulator import ToolCallAccumulator, ToolCallAccumulatorError


class OpenAIRequestMapper:
    """Converts internal ProviderRequestContext into OpenAI Chat Completions JSON dictionary."""

    @classmethod
    def map_request(
        cls,
        context: ProviderRequestContext,
        model: str,
        stream: bool = True,
        stream_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convert ProviderRequestContext into an OpenAI-compatible POST body dictionary.

        Args:
            context: The assembled request context (messages, tools, system prompt).
            model: Target model identifier.
            stream: Whether to request streaming (default True).
            stream_options: Optional stream options (e.g. {"include_usage": True}).

        Returns:
            Dictionary matching OpenAI Chat Completions specification.
        """
        mapped_messages: List[Dict[str, Any]] = []

        for msg in context.messages:
            mapped_messages.append(cls.map_message(msg))

        body: Dict[str, Any] = {
            "model": model,
            "messages": mapped_messages,
            "stream": stream,
        }

        # Tools support
        if context.tools:
            body["tools"] = list(context.tools)
            body["parallel_tool_calls"] = False

        if stream_options:
            body["stream_options"] = stream_options

        return body

    @classmethod
    def map_message(cls, message: ChatMessage) -> Dict[str, Any]:
        """Convert an internal ChatMessage into an OpenAI Chat Completion message dict."""
        role = message.role

        if role == Role.SYSTEM:
            return {
                "role": "system",
                "content": message.content or "",
            }

        elif role == Role.USER:
            return {
                "role": "user",
                "content": message.content or "",
            }

        elif role == Role.ASSISTANT:
            d: Dict[str, Any] = {
                "role": "assistant",
                "content": message.content,
            }
            if message.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.call_id,
                        "type": "function",
                        "function": {
                            "name": tc.tool_name,
                            "arguments": json.dumps(
                                tc.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                        },
                    }
                    for tc in message.tool_calls
                ]
            return d

        elif role == Role.TOOL:
            d = {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content or "",
            }
            if message.name:
                d["name"] = message.name
            return d

        else:
            return {
                "role": str(role.value if hasattr(role, "value") else role),
                "content": message.content or "",
            }


class OpenAICompatibleProvider:
    """Independent OpenAI-compatible streaming LLM provider."""

    def __init__(
        self,
        config: Config,
        http_client: Optional[HttpClient] = None,
        online_access: bool = True,
    ):
        self.config = config
        effective_timeout = getattr(config, "timeout_seconds", getattr(config, "timeout", 30.0))
        self.http_client = http_client or HttpClient(
            base_url=config.base_url,
            timeout=effective_timeout,
        )
        self.online_access = online_access
        self.accumulator = ToolCallAccumulator()
        self.last_tool_calls: List[ToolCall] = []

    def stream_chat(
        self,
        context: ProviderRequestContext,
        turn_id: str = "turn-1",
        cancel_event: Optional[threading.Event] = None,
        online_access: Optional[bool] = None,
    ) -> Iterator[ProviderStreamEvent]:
        """Stream chat completions from an OpenAI-compatible endpoint.

        Args:
            context: Assembled context with messages, tools, and system prompt.
            turn_id: Active agent turn identifier.
            cancel_event: Optional threading.Event for in-flight cancellation.
            online_access: Optional override for online network permission policy.

        Yields:
            ProviderStreamEvent instances (TextDelta, ToolCallDelta, ProviderCompleted, ProviderError).
        """
        effective_online_access = (
            online_access if online_access is not None else self.online_access
        )

        # 0. Configuration check
        if not self.config or not getattr(self.config, "base_url", None) or not self.config.base_url.strip():
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.CONFIGURATION_ERROR,
                message="AI provider is not configured. Please set Base URL in Blender Preferences.",
            )
            return

        if not getattr(self.config, "model", None) or not self.config.model.strip():
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.CONFIGURATION_ERROR,
                message="AI model is not configured. Please set Model in Blender Preferences.",
            )
            return

        # 1. Network policy check
        allowed, reason = is_network_allowed(self.config.base_url, effective_online_access)
        if not allowed:
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.NETWORK_ERROR,
                message=f"Network access forbidden: {reason}",
            )
            return

        # 2. Check pre-request cancellation
        if cancel_event and cancel_event.is_set():
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.CANCELLED,
                message="Request cancelled before start.",
            )
            return

        # 3. Assemble request payload
        req_dict = OpenAIRequestMapper.map_request(
            context=context,
            model=self.config.model,
            stream=True,
        )
        payload_bytes = json.dumps(
            req_dict,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        # 4. Prepare headers (hygiene: omit Authorization if key empty)
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.config.api_key and self.config.api_key.strip():
            headers["Authorization"] = f"Bearer {self.config.api_key.strip()}"

        # 5. Dispatch HTTP POST request
        try:
            resp = self.http_client.post(
                endpoint="/chat/completions",
                payload=payload_bytes,
                headers=headers,
                cancel_event=cancel_event,
                timeout=getattr(self.config, "timeout_seconds", getattr(self.config, "timeout", 30.0)),
            )
        except HttpError as err:
            err_type = self._map_http_status_to_error_type(err.status_code)
            yield ProviderError(
                turn_id=turn_id,
                type=err_type,
                message=f"HTTP {err.status_code}: {err.message}",
                details={"status_code": err.status_code, "body_snippet": err.body_snippet},
            )
            return
        except HttpTimeoutError as err:
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.TIMEOUT,
                message=err.message,
            )
            return
        except (HttpConnectionError, NetworkError) as err:
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.NETWORK_ERROR,
                message=f"Cannot connect to AI provider at {self.config.base_url}. Ensure the server (e.g. 9Router, Ollama) is running.",
                details={"error": err.message},
            )
            return
        except Exception as err:
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.NETWORK_ERROR,
                message=f"Unexpected transport failure: {err}",
            )
            return

        # 6. Stream and normalize chunks via SSEParser and ToolCallAccumulator
        parser = SSEParser()
        self.accumulator.reset()
        self.last_tool_calls = []
        finish_reason: Optional[str] = None
        usage: Optional[Dict[str, int]] = None

        with resp:
            for raw_chunk in resp:
                if cancel_event and cancel_event.is_set():
                    yield ProviderError(
                        turn_id=turn_id,
                        type=ProviderErrorType.CANCELLED,
                        message="Stream cancelled by user.",
                    )
                    return

                try:
                    events = parser.feed(raw_chunk)
                except SSEParseError as err:
                    yield ProviderError(
                        turn_id=turn_id,
                        type=ProviderErrorType.INVALID_RESPONSE,
                        message=f"SSE stream parsing error: {err.message}",
                    )
                    return

                for payload_str in events:
                    if payload_str == "[DONE]":
                        break

                    try:
                        chunk_data = json.loads(payload_str)
                    except json.JSONDecodeError as exc:
                        yield ProviderError(
                            turn_id=turn_id,
                            type=ProviderErrorType.INVALID_RESPONSE,
                            message=f"Malformed JSON in SSE payload: {exc}",
                        )
                        return

                    # Extract usage if present
                    if "usage" in chunk_data and isinstance(chunk_data["usage"], dict):
                        usage = chunk_data["usage"]

                    choices = chunk_data.get("choices")
                    if not choices or not isinstance(choices, list):
                        continue

                    choice = choices[0]
                    if not isinstance(choice, dict):
                        continue

                    # Capture finish_reason if set
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

                    delta = choice.get("delta")
                    if not delta or not isinstance(delta, dict):
                        continue

                    # 1. Text delta
                    text_content = delta.get("content")
                    if text_content and isinstance(text_content, str):
                        yield TextDelta(turn_id=turn_id, text=text_content)

                    # 2. Tool call deltas
                    tc_deltas = delta.get("tool_calls")
                    if tc_deltas and isinstance(tc_deltas, list):
                        for tc_dict in tc_deltas:
                            if not isinstance(tc_dict, dict):
                                continue

                            idx = tc_dict.get("index", 0)
                            call_id = tc_dict.get("id")
                            fn_dict = tc_dict.get("function") or {}

                            name_delta = fn_dict.get("name") if isinstance(fn_dict, dict) else None
                            args_delta = fn_dict.get("arguments") if isinstance(fn_dict, dict) else None

                            tc_event = ToolCallDelta(
                                turn_id=turn_id,
                                index=idx,
                                call_id=call_id,
                                tool_name_delta=name_delta,
                                arguments_delta=args_delta,
                            )
                            self.accumulator.feed_delta(tc_event)
                            yield tc_event

            # Flush trailing SSE events if any
            try:
                trailing = parser.close()
                for payload_str in trailing:
                    if payload_str == "[DONE]":
                        continue
                    try:
                        data = json.loads(payload_str)
                        if "usage" in data and isinstance(data["usage"], dict):
                            usage = data["usage"]
                    except Exception:
                        pass
            except SSEParseError as err:
                yield ProviderError(
                    turn_id=turn_id,
                    type=ProviderErrorType.INVALID_RESPONSE,
                    message=f"SSE stream termination error: {err.message}",
                )
                return

        # 7. Final cancellation check
        if cancel_event and cancel_event.is_set():
            yield ProviderError(
                turn_id=turn_id,
                type=ProviderErrorType.CANCELLED,
                message="Stream cancelled by user.",
            )
            return

        # 8. Reassemble and validate accumulated tool calls if any were streamed
        if self.accumulator._calls:
            try:
                self.last_tool_calls = self.accumulator.finalize()
            except ToolCallAccumulatorError as exc:
                yield ProviderError(
                    turn_id=turn_id,
                    type=ProviderErrorType.TOOL_CALL_PARSE_ERROR,
                    message=exc.message,
                )
                return

        # 9. Yield final completion event
        effective_finish = finish_reason or ("tool_calls" if self.last_tool_calls else "stop")
        yield ProviderCompleted(
            turn_id=turn_id,
            finish_reason=effective_finish,
            usage=usage,
        )

    @staticmethod
    def _map_http_status_to_error_type(status_code: int) -> Union[str, ProviderErrorType]:
        """Map HTTP status codes to ProviderErrorType."""
        if status_code in (401, 403):
            return ProviderErrorType.AUTH_ERROR
        elif status_code == 429:
            return ProviderErrorType.RATE_LIMIT
        elif 500 <= status_code <= 599:
            return "UNAVAILABLE"
        return ProviderErrorType.NETWORK_ERROR
