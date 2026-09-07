"""Asynchronous background worker for AI provider tasks.

Strictly ZERO Blender (bpy) dependencies. Pure Python.
Runs in a background thread and posts structured events to ThreadSafeEventQueue.
"""

from dataclasses import dataclass
from queue import Empty, Queue
import threading
from typing import Any, List, Optional

from core.events import (
    AgentErrorEvent,
    ProviderResponseReadyEvent,
    StreamingTextDeltaEvent,
)
from core.event_queue import ThreadSafeEventQueue
from core.types import ToolResult
from agent.models import (
    ProviderCompleted,
    ProviderError,
    ProviderErrorType,
    ProviderResponse,
    TextDelta,
    ToolCallDelta,
)
from agent.provider import BaseProvider


@dataclass
class WorkerTask:
    """Represents a discrete generation task assigned to the worker thread."""

    turn_id: str
    prompt: str
    tool_results: Optional[List[ToolResult]]
    cancel_event: threading.Event
    context: Optional[Any] = None


class AgentWorker:
    """Manages the background execution thread for AI provider operations."""

    def __init__(self, provider: Any, event_queue: ThreadSafeEventQueue):
        self.provider = provider
        self.event_queue = event_queue
        self._task_queue: Queue[WorkerTask] = Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        """Check whether the worker background thread is active."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the background worker thread."""
        if self.is_running:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="AISidebarWorker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 0.5) -> None:
        """Signal the worker to terminate and wait up to timeout seconds.

        Guarantees that Blender is never hung on exit or addon unregister.
        """
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def submit_task(
        self,
        turn_id: str,
        prompt: str,
        tool_results: Optional[List[ToolResult]] = None,
        cancel_event: Optional[threading.Event] = None,
        context: Optional[Any] = None,
    ) -> None:
        """Submit a new provider generation task to the worker queue."""
        if not self.is_running:
            self.start()

        task = WorkerTask(
            turn_id=turn_id,
            prompt=prompt,
            tool_results=tool_results,
            cancel_event=cancel_event or threading.Event(),
            context=context,
        )
        self._task_queue.put(task)

    def _run_loop(self) -> None:
        """Main execution loop of the worker thread."""
        while not self._stop_event.is_set():
            try:
                task = self._task_queue.get(timeout=0.05)
            except Empty:
                continue

            # 1. Check if cancelled before execution
            if task.cancel_event.is_set():
                continue

            # 2. Execute provider generation off the main thread
            try:
                if hasattr(self.provider, "stream_chat") and task.context is not None:
                    streamed_text = ""
                    cancelled = False

                    for stream_ev in self.provider.stream_chat(
                        context=task.context,
                        turn_id=task.turn_id,
                        cancel_event=task.cancel_event,
                    ):
                        if task.cancel_event.is_set():
                            cancelled = True
                            break

                        if isinstance(stream_ev, TextDelta):
                            streamed_text += stream_ev.text
                            self.event_queue.put(
                                StreamingTextDeltaEvent(
                                    delta=stream_ev.text,
                                    turn_id=task.turn_id,
                                )
                            )
                        elif isinstance(stream_ev, ToolCallDelta):
                            # Streaming tool call accumulation is handled inside the provider accumulator
                            pass
                        elif isinstance(stream_ev, ProviderError):
                            if stream_ev.type == ProviderErrorType.CANCELLED or task.cancel_event.is_set():
                                cancelled = True
                                break
                            type_str = (
                                stream_ev.type.value
                                if hasattr(stream_ev.type, "value")
                                else str(stream_ev.type)
                            )
                            self.event_queue.put(
                                AgentErrorEvent(
                                    error_type=f"PROVIDER_{type_str}",
                                    message=stream_ev.message,
                                    turn_id=task.turn_id,
                                    details=stream_ev.details or {},
                                )
                            )
                            cancelled = True
                            break
                        elif isinstance(stream_ev, ProviderCompleted):
                            if task.cancel_event.is_set():
                                cancelled = True
                                break

                            tool_calls = list(getattr(self.provider, "last_tool_calls", []))
                            is_final = (stream_ev.finish_reason == "stop" or not tool_calls)
                            response = ProviderResponse(
                                assistant_text=streamed_text if streamed_text else None,
                                tool_calls=tool_calls,
                                is_final=is_final,
                            )
                            self.event_queue.put(
                                ProviderResponseReadyEvent(
                                    response=response,
                                    turn_id=task.turn_id,
                                )
                            )
                            break

                    if cancelled:
                        continue

                else:
                    # Fallback to synchronous generate() for MockProvider / M1
                    response = self.provider.generate(
                        prompt=task.prompt,
                        tool_results=task.tool_results,
                    )

                    # 3. Check if cancelled during generation
                    if task.cancel_event.is_set():
                        continue

                    # 4. Enqueue successful response event for main thread consumption
                    self.event_queue.put(
                        ProviderResponseReadyEvent(
                            response=response,
                            turn_id=task.turn_id,
                        )
                    )

            except Exception as exc:
                # 5. Isolate worker exceptions — never crash Blender UI
                self.event_queue.put(
                    AgentErrorEvent(
                        error_type="WORKER_EXCEPTION",
                        message=str(exc),
                        turn_id=task.turn_id,
                        details={"exception_class": exc.__class__.__name__},
                    )
                )
