"""AgentRuntime coordinating the execution loop across Provider, Dispatcher, and State Machine.

Main-thread-owned state coordinator supporting both synchronous turns and
asynchronous event-driven execution across background worker threads.
Zero Blender (bpy) dependencies. Pure Python.
"""

import json
import time
import threading
from typing import Any, List, Optional, Union

from core.events import (
    AgentErrorEvent,
    CancelRequestedEvent,
    Event,
    FinalResponseReadyEvent,
    PromptSubmittedEvent,
    ProviderResponseReadyEvent,
    ShutdownEvent,
    StreamingTextDeltaEvent,
    ToolResultReadyEvent,
    TurnMetrics,
)
from core.event_queue import ThreadSafeEventQueue
from core.types import ToolResult
from agent.context_builder import ContextBuilder
from agent.dispatcher import ToolDispatcher
from agent.history import HistoryKind, RuntimeHistory
from agent.models import (
    AgentResult,
    ChatMessage,
    Conversation,
    ProviderCompleted,
    ProviderError,
    ProviderErrorType,
    ProviderResponse,
    ProviderStreamEvent,
    Role,
    TextDelta,
    ToolCall,
    ToolCallDelta,
)
from agent.provider import BaseProvider
from agent.state_machine import AgentState, AgentStateMachine
from agent.worker import AgentWorker


class AgentRuntime:
    """Agent execution coordinator owning lifecycle state on the main thread."""

    def __init__(
        self,
        provider: Any,
        dispatcher: ToolDispatcher,
        event_queue: Optional[ThreadSafeEventQueue] = None,
        worker: Optional[AgentWorker] = None,
        max_tool_rounds: int = 5,
    ):
        self.provider = provider
        self.dispatcher = dispatcher
        self.state_machine = AgentStateMachine()

        self.event_queue = event_queue or ThreadSafeEventQueue()
        self.worker = worker or AgentWorker(provider=self.provider, event_queue=self.event_queue)
        self.history = RuntimeHistory()

        # Session Conversation & multi-round loop guard
        self.conversation = Conversation()
        self.max_tool_rounds: int = max_tool_rounds
        self._current_tool_round: int = 0
        self._streaming_text: str = ""

        # Main-thread owned session state
        self._turn_counter: int = 0
        self._current_turn_id: Optional[str] = None
        self._current_cancel_event: Optional[threading.Event] = None
        self._current_prompt: str = ""
        self._current_tool_results: List[ToolResult] = []
        self._current_metrics: Optional[TurnMetrics] = None
        self._last_result: Optional[AgentResult] = None
        self._stale_events_count: int = 0

    @property
    def current_state(self) -> AgentState:
        """Get the current agent state."""
        return self.state_machine.current_state

    @property
    def current_turn_id(self) -> Optional[str]:
        """Get the active turn identifier, if any."""
        return self._current_turn_id

    @property
    def last_result(self) -> Optional[AgentResult]:
        """Get the most recent AgentResult."""
        return self._last_result

    @property
    def current_metrics(self) -> Optional[TurnMetrics]:
        """Get metrics for the current or last completed turn."""
        return self._current_metrics

    @property
    def stale_events_count(self) -> int:
        """Count of stale events rejected due to turn_id mismatch."""
        return self._stale_events_count

    # -------------------------------------------------------------------------
    # Asynchronous Event-Driven API (Phase 6 / M2.7)
    # -------------------------------------------------------------------------

    def submit_prompt(self, prompt: str) -> str:
        """Submit a prompt for asynchronous background processing.

        Allocates a monotonic turn_id, transitions to PROCESSING,
        and delegates generation to the background worker.

        Args:
            prompt: User natural language prompt.

        Returns:
            The allocated turn_id string.
        """
        # Reject concurrent submissions if active turn is in progress
        if self.state_machine.current_state in (AgentState.PROCESSING, AgentState.EXECUTING_TOOL):
            raise RuntimeError("Active turn in progress. Concurrent submission rejected.")

        self._turn_counter += 1
        turn_id = f"turn_{self._turn_counter}"
        self._current_turn_id = turn_id
        self._current_prompt = prompt
        self._current_tool_results = []
        self._current_tool_round = 0
        self._streaming_text = ""
        self._current_cancel_event = threading.Event()
        self._current_metrics = TurnMetrics(turn_id=turn_id, t_submitted=time.time())

        # Record user prompt in session Conversation
        self.conversation.add_message(ChatMessage(role=Role.USER, content=prompt))

        self.state_machine.transition_to(AgentState.PROCESSING)
        self.event_queue.put(PromptSubmittedEvent(prompt=prompt, turn_id=turn_id))

        self.history.add(
            item_id=f"{turn_id}_user",
            turn_id=turn_id,
            kind=HistoryKind.USER,
            title=f"User: {prompt[:36]}",
            status="SENT",
            summary=prompt,
            detail=prompt,
        )

        context = None
        if hasattr(self.provider, "stream_chat"):
            tools = self.dispatcher.registry.list()
            context = ContextBuilder.build(
                conversation=self.conversation,
                tools=tools,
            )

        self.worker.submit_task(
            turn_id=turn_id,
            prompt=prompt,
            tool_results=None,
            cancel_event=self._current_cancel_event,
            context=context,
        )
        return turn_id

    def process_event(self, event: Any) -> Optional[AgentResult]:
        """Process an event on the main thread (called by timer pump).

        Enforces stale event filtering, tool dispatch on main thread,
        state transitions, and final result delivery.

        Args:
            event: Event dequeued from ThreadSafeEventQueue or direct ProviderStreamEvent.

        Returns:
            AgentResult if turn completed on this event, else None.
        """
        # 1. Stale event filtering: reject events from old or cancelled turns
        event_turn_id = getattr(event, "turn_id", None)
        if event_turn_id is not None and event_turn_id != self._current_turn_id:
            # System events (e.g. shutdown) are exempt from turn matching
            if not isinstance(event, (ShutdownEvent,)):
                self._stale_events_count += 1
                return None

        # 2. Track first event latency
        if self._current_metrics and self._current_metrics.t_first_event is None:
            self._current_metrics.t_first_event = getattr(event, "timestamp", None) or time.time()

        # 3. Handle Streaming Text Delta (from Worker event or direct ProviderStreamEvent)
        if isinstance(event, StreamingTextDeltaEvent):
            self._streaming_text += event.delta
            return None
        elif isinstance(event, TextDelta):
            self._streaming_text += event.text
            return None
        elif isinstance(event, ToolCallDelta):
            return None

        # 4. Handle direct ProviderError stream event
        if isinstance(event, ProviderError):
            if event.type == ProviderErrorType.CANCELLED or (
                self._current_cancel_event and self._current_cancel_event.is_set()
            ):
                return None

            self.state_machine.transition_to(AgentState.ERROR)
            if self._current_metrics:
                self._current_metrics.t_completed = time.time()

            type_str = event.type.value if hasattr(event.type, "value") else str(event.type)
            err_result = AgentResult(
                final_text=f"Error: {event.message}",
                tool_results=list(self._current_tool_results),
                state=AgentState.ERROR.value,
            )
            self._last_result = err_result
            self.history.add(
                item_id=f"{event.turn_id}_error",
                turn_id=event.turn_id,
                kind=HistoryKind.ERROR,
                title=f"Error: PROVIDER_{type_str}",
                status="ERROR",
                summary=event.message,
                detail=f"Provider Error: {event.message}\nDetails: {event.details}",
            )
            self.event_queue.put(
                AgentErrorEvent(
                    error_type=f"PROVIDER_{type_str}",
                    message=event.message,
                    turn_id=event.turn_id,
                    details=event.details or {},
                )
            )
            self._current_turn_id = None
            return err_result

        # 5. Handle direct ProviderCompleted stream event (normalize into ProviderResponseReadyEvent)
        if isinstance(event, ProviderCompleted):
            tool_calls = list(getattr(self.provider, "last_tool_calls", []))
            is_final = (event.finish_reason == "stop" or not tool_calls)
            resp = ProviderResponse(
                assistant_text=self._streaming_text if self._streaming_text else None,
                tool_calls=tool_calls,
                is_final=is_final,
            )
            event = ProviderResponseReadyEvent(response=resp, turn_id=event.turn_id)

        # 6. Handle Provider response (from worker or direct completion)
        if isinstance(event, ProviderResponseReadyEvent):
            if self._current_cancel_event and self._current_cancel_event.is_set():
                return None

            resp = event.response
            if resp is None or resp.is_final or not resp.tool_calls:
                # Turn finished without tool calls (or final synthesis completed)
                self.state_machine.transition_to(AgentState.IDLE)
                if self._current_metrics:
                    self._current_metrics.t_completed = time.time()

                final_text = (
                    self._streaming_text
                    if self._streaming_text
                    else ((resp.assistant_text if resp else None) or "İşlem tamamlandı.")
                )
                self._streaming_text = ""

                # Record final assistant message in Conversation
                self.conversation.add_message(
                    ChatMessage(
                        role=Role.ASSISTANT,
                        content=final_text,
                    )
                )

                result = AgentResult(
                    final_text=final_text,
                    tool_results=list(self._current_tool_results),
                    state=AgentState.IDLE.value,
                )
                self._last_result = result
                self.history.add(
                    item_id=f"{event.turn_id}_assistant",
                    turn_id=event.turn_id,
                    kind=HistoryKind.ASSISTANT,
                    title=f"Assistant: {final_text[:36]}",
                    status="DONE",
                    summary=final_text[:120],
                    detail=final_text,
                )
                self.event_queue.put(
                    FinalResponseReadyEvent(
                        final_text=final_text,
                        turn_id=event.turn_id,
                        tool_results=self._current_tool_results,
                        metrics=self._current_metrics,
                    )
                )
                self._current_turn_id = None
                return result

            # Tool calls requested:
            # 6.1 Check max tool rounds loop guard
            self._current_tool_round += 1
            if self._current_tool_round > self.max_tool_rounds:
                self.state_machine.transition_to(AgentState.ERROR)
                if self._current_metrics:
                    self._current_metrics.t_completed = time.time()

                err_msg = f"Maximum tool rounds limit reached ({self.max_tool_rounds})."
                error_result = AgentResult(
                    final_text=f"Error: {err_msg}",
                    tool_results=list(self._current_tool_results),
                    state=AgentState.ERROR.value,
                )
                self._last_result = error_result
                self.history.add(
                    item_id=f"{event.turn_id}_error",
                    turn_id=event.turn_id,
                    kind=HistoryKind.ERROR,
                    title="Error: MAX_TOOL_ROUNDS_EXCEEDED",
                    status="ERROR",
                    summary=err_msg,
                    detail=f"Loop guard triggered: {err_msg}",
                )
                self.event_queue.put(
                    AgentErrorEvent(
                        error_type="MAX_TOOL_ROUNDS_EXCEEDED",
                        message=err_msg,
                        turn_id=event.turn_id,
                        details={
                            "max_rounds": self.max_tool_rounds,
                            "current_round": self._current_tool_round,
                        },
                    )
                )
                self._current_turn_id = None
                return error_result

            # 6.2 Record assistant message with tool calls in Conversation
            asst_text = self._streaming_text if self._streaming_text else (resp.assistant_text or None)
            self.conversation.add_message(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=asst_text,
                    tool_calls=resp.tool_calls,
                )
            )
            self._streaming_text = ""

            # 6.3 Execute tool calls sequentially on Main Thread
            t_tools_start = time.perf_counter()
            for tool_call in resp.tool_calls:
                if self._current_cancel_event and self._current_cancel_event.is_set():
                    return None

                self.state_machine.transition_to(AgentState.EXECUTING_TOOL)
                tool_res = self.dispatcher.dispatch(tool_call)
                self._current_tool_results.append(tool_res)

                args_str = (
                    json.dumps(tool_call.arguments, indent=2, sort_keys=True)
                    if tool_call.arguments
                    else "{}"
                )
                if tool_res.success:
                    res_str = json.dumps(tool_res.data, indent=2, sort_keys=True)
                    status_badge = "OK"
                    summary_str = f"{tool_call.tool_name} completed."
                    tool_content = json.dumps(tool_res.data, ensure_ascii=False)
                else:
                    res_str = f"Error: {tool_res.error.message}\nType: {tool_res.error.type}"
                    status_badge = "FAIL"
                    summary_str = f"Error: {tool_res.error.message}"
                    tool_content = json.dumps(
                        {"error": tool_res.error.message, "type": tool_res.error.type},
                        ensure_ascii=False,
                    )
                detail_str = f"Tool: {tool_call.tool_name}\nArguments:\n{args_str}\n\nResult:\n{res_str}"

                # Append tool result to Conversation
                self.conversation.add_message(
                    ChatMessage(
                        role=Role.TOOL,
                        content=tool_content,
                        tool_call_id=tool_call.call_id,
                        name=tool_call.tool_name,
                    )
                )

                self.history.add(
                    item_id=f"{event.turn_id}_tool_{len(self._current_tool_results)}",
                    turn_id=event.turn_id,
                    kind=HistoryKind.TOOL,
                    title=f"Tool: {tool_call.tool_name}",
                    status=status_badge,
                    summary=summary_str,
                    detail=detail_str,
                )
                self.event_queue.put(ToolResultReadyEvent(tool_result=tool_res, turn_id=event.turn_id))

                if not tool_res.success:
                    # Tool failure: transition to ERROR and terminate
                    self.state_machine.transition_to(AgentState.ERROR)
                    if self._current_metrics:
                        self._current_metrics.t_completed = time.time()

                    terminal_text = f"Tool failure: {tool_res.error.message}"
                    error_result = AgentResult(
                        final_text=terminal_text,
                        tool_results=list(self._current_tool_results),
                        state=AgentState.ERROR.value,
                    )
                    self._last_result = error_result
                    self.event_queue.put(
                        AgentErrorEvent(
                            error_type="TOOL_FAILURE",
                            message=tool_res.error.message,
                            turn_id=event.turn_id,
                            details=tool_res.error.details,
                        )
                    )
                    self._current_turn_id = None
                    return error_result

                # Return to PROCESSING between tool executions
                self.state_machine.transition_to(AgentState.PROCESSING)

            if self._current_metrics:
                self._current_metrics.t_tools_duration += (time.perf_counter() - t_tools_start)

            if self._current_cancel_event and self._current_cancel_event.is_set():
                return None

            # 6.4 Build updated Context and delegate next LLM step to worker
            context = None
            if hasattr(self.provider, "stream_chat"):
                tools = self.dispatcher.registry.list()
                context = ContextBuilder.build(
                    conversation=self.conversation,
                    tools=tools,
                )

            self.worker.submit_task(
                turn_id=event.turn_id,
                prompt=self._current_prompt,
                tool_results=self._current_tool_results,
                cancel_event=self._current_cancel_event,
                context=context,
            )
            return None

        # 7. Handle Worker/System Errors
        if isinstance(event, AgentErrorEvent):
            self.state_machine.transition_to(AgentState.ERROR)
            if self._current_metrics:
                self._current_metrics.t_completed = time.time()

            self.history.add(
                item_id=f"{event.turn_id}_error",
                turn_id=event.turn_id,
                kind=HistoryKind.ERROR,
                title=f"Error: {event.error_type}",
                status="ERROR",
                summary=event.message,
                detail=f"Error Type: {event.error_type}\nMessage: {event.message}\nDetails: {event.details}",
            )
            err_result = AgentResult(
                final_text=f"Error: {event.message}",
                tool_results=list(self._current_tool_results),
                state=AgentState.ERROR.value,
            )
            self._last_result = err_result
            self._current_turn_id = None
            return err_result

        return None

    def cancel_current_turn(self) -> None:
        """Cancel the currently active turn, discarding pending events."""
        if self._current_turn_id is not None:
            if self._current_cancel_event:
                self._current_cancel_event.set()
            cancelled_turn = self._current_turn_id
            self._current_turn_id = None
            self.history.add(
                item_id=f"{cancelled_turn}_cancel",
                turn_id=cancelled_turn,
                kind=HistoryKind.SYSTEM,
                title="Cancelled",
                status="CANCEL",
                summary=f"Turn '{cancelled_turn}' was cancelled.",
                detail=f"Turn '{cancelled_turn}' was cancelled by user.",
            )
            self.event_queue.put(CancelRequestedEvent(turn_id=cancelled_turn))
            if self.state_machine.current_state != AgentState.IDLE:
                self.state_machine.reset()

    def clear_history(self) -> None:
        """Clear session conversation and tool execution history."""
        self.history.clear()
        self.conversation.clear()

    def shutdown(self) -> None:
        """Gracefully terminate background workers and queue."""
        self.cancel_current_turn()
        if self.worker:
            self.worker.stop(timeout=0.5)
        self.event_queue.put(ShutdownEvent())

    # -------------------------------------------------------------------------
    # Synchronous Execution API (Phase 5 / M2 Compatibility)
    # -------------------------------------------------------------------------

    def run(self, prompt: str) -> AgentResult:
        """Execute a full turn synchronously.

        Supports both MockProvider (generate) and real providers (stream_chat).
        """
        if hasattr(self.provider, "stream_chat"):
            self.state_machine.transition_to(AgentState.PROCESSING)
            self.conversation.add_message(ChatMessage(role=Role.USER, content=prompt))
            tool_results: List[ToolResult] = []
            current_round = 0
            tools = self.dispatcher.registry.list()

            while True:
                context = ContextBuilder.build(conversation=self.conversation, tools=tools)
                streamed_text = ""
                provider_error = None

                for stream_ev in self.provider.stream_chat(context=context, turn_id="sync_turn"):
                    if isinstance(stream_ev, TextDelta):
                        streamed_text += stream_ev.text
                    elif isinstance(stream_ev, ProviderError):
                        provider_error = stream_ev
                        break

                if provider_error:
                    self.state_machine.transition_to(AgentState.ERROR)
                    return AgentResult(
                        final_text=f"Error: {provider_error.message}",
                        tool_results=tool_results,
                        state=AgentState.ERROR.value,
                    )

                tool_calls = list(getattr(self.provider, "last_tool_calls", []))
                if not tool_calls:
                    final_text = streamed_text or "İşlem tamamlandı."
                    self.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=final_text))
                    self.state_machine.transition_to(AgentState.IDLE)
                    return AgentResult(
                        final_text=final_text,
                        tool_results=tool_results,
                        state=AgentState.IDLE.value,
                    )

                current_round += 1
                if current_round > self.max_tool_rounds:
                    self.state_machine.transition_to(AgentState.ERROR)
                    return AgentResult(
                        final_text=f"Error: Maximum tool rounds limit reached ({self.max_tool_rounds}).",
                        tool_results=tool_results,
                        state=AgentState.ERROR.value,
                    )

                self.conversation.add_message(
                    ChatMessage(
                        role=Role.ASSISTANT,
                        content=streamed_text or None,
                        tool_calls=tool_calls,
                    )
                )

                for tc in tool_calls:
                    self.state_machine.transition_to(AgentState.EXECUTING_TOOL)
                    res = self.dispatcher.dispatch(tc)
                    tool_results.append(res)
                    tc_content = (
                        json.dumps(res.data, ensure_ascii=False)
                        if res.success
                        else json.dumps({"error": res.error.message, "type": res.error.type}, ensure_ascii=False)
                    )
                    self.conversation.add_message(
                        ChatMessage(
                            role=Role.TOOL,
                            content=tc_content,
                            tool_call_id=tc.call_id,
                            name=tc.tool_name,
                        )
                    )

                    if not res.success:
                        self.state_machine.transition_to(AgentState.ERROR)
                        return AgentResult(
                            final_text=f"Tool failure: {res.error.message}",
                            tool_results=tool_results,
                            state=AgentState.ERROR.value,
                        )
                    self.state_machine.transition_to(AgentState.PROCESSING)

        else:
            # Fallback for MockProvider (Phase 5)
            self.state_machine.transition_to(AgentState.PROCESSING)
            initial_response = self.provider.generate(prompt=prompt)

            if initial_response.is_final or not initial_response.tool_calls:
                self.state_machine.transition_to(AgentState.IDLE)
                return AgentResult(
                    final_text=initial_response.assistant_text or "",
                    tool_results=[],
                    state=AgentState.IDLE.value,
                )

            tool_results: List[ToolResult] = []
            for tool_call in initial_response.tool_calls:
                self.state_machine.transition_to(AgentState.EXECUTING_TOOL)
                result = self.dispatcher.dispatch(tool_call)
                tool_results.append(result)

                if not result.success:
                    self.state_machine.transition_to(AgentState.ERROR)
                    error_response = self.provider.generate(prompt=prompt, tool_results=tool_results)
                    terminal_text = error_response.assistant_text or f"Tool failure: {result.error.message}"
                    return AgentResult(
                        final_text=terminal_text,
                        tool_results=tool_results,
                        state=AgentState.ERROR.value,
                    )

                self.state_machine.transition_to(AgentState.PROCESSING)

            final_response = self.provider.generate(prompt=prompt, tool_results=tool_results)
            self.state_machine.transition_to(AgentState.IDLE)

            return AgentResult(
                final_text=final_response.assistant_text or "İşlem tamamlandı.",
                tool_results=tool_results,
                state=AgentState.IDLE.value,
            )

    def reset(self) -> None:
        """Reset the runtime and state machine."""
        self.cancel_current_turn()
        self.conversation.clear()
        self.state_machine.reset()
