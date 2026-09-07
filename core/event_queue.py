"""Thread-safe event queue with bounded batch draining to prevent UI thread starvation.

Zero Blender dependencies. Pure Python.
"""

from queue import Empty, Queue
import time
from typing import List, Optional

from core.events import Event


class ThreadSafeEventQueue:
    """Thread-safe FIFO queue for events traversing the worker/main-thread boundary."""

    def __init__(self, maxsize: int = 0):
        self._queue: Queue = Queue(maxsize=maxsize)

    def put(self, event: Event) -> None:
        """Enqueue an event from any thread."""
        self._queue.put(event)

    def get_nowait(self) -> Optional[Event]:
        """Try to dequeue a single event without blocking."""
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def drain_batch(
        self,
        max_items: int = 10,
        max_time_sec: float = 0.005,
    ) -> List[Event]:
        """Drain a bounded batch of events.

        Stops when either max_items is reached, queue is empty,
        or max_time_sec has elapsed. This guarantees that Blender's
        main event loop is never starved by a large queue backlog.

        Args:
            max_items: Maximum number of events to process in one tick.
            max_time_sec: Maximum wall-clock time in seconds to spend draining.

        Returns:
            List of events drained in FIFO order.
        """
        batch: List[Event] = []
        start_time = time.perf_counter()

        while len(batch) < max_items:
            # Check elapsed time budget
            if (time.perf_counter() - start_time) >= max_time_sec:
                break

            try:
                event = self._queue.get_nowait()
                batch.append(event)
            except Empty:
                break

        return batch

    def qsize(self) -> int:
        """Return approximate queue size."""
        return self._queue.qsize()

    def is_empty(self) -> bool:
        """Return True if the queue is empty."""
        return self._queue.empty()

    def clear(self) -> int:
        """Drain and discard all items from the queue.

        Returns:
            Number of discarded events.
        """
        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
                discarded += 1
            except Empty:
                break
        return discarded
