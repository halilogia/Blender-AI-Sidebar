"""FIFO queue for user prompts waiting behind an active agent turn.

This queue is intentionally separate from the worker/event queue. Worker
tasks belong to one already-started turn; queued prompts belong to the user
conversation and are dispatched by AgentRuntime at a lifecycle boundary.
"""

from collections import deque
from dataclasses import dataclass, field
import time
from typing import Deque, List, Optional


@dataclass(frozen=True)
class QueuedPrompt:
    """A prompt waiting to be started by the runtime."""

    queue_id: str
    prompt: str
    image_id: Optional[str] = None
    expected_visual_description: Optional[str] = None
    enqueued_at: float = field(default_factory=time.time)
    history_item_id: str = ""


class PromptQueue:
    """Small bounded FIFO queue for user-submitted prompts."""

    def __init__(self, maxsize: int = 20):
        self.maxsize = max(1, int(maxsize))
        self._items: Deque[QueuedPrompt] = deque()
        self._counter = 0

    def enqueue(
        self,
        prompt: str,
        image_id: Optional[str] = None,
        expected_visual_description: Optional[str] = None,
    ) -> QueuedPrompt:
        """Append a prompt or raise OverflowError when the queue is full."""
        if len(self._items) >= self.maxsize:
            raise OverflowError(f"Prompt queue is full (max {self.maxsize}).")
        self._counter += 1
        queue_id = f"queued_{self._counter}"
        item = QueuedPrompt(
            queue_id=queue_id,
            prompt=prompt,
            image_id=image_id,
            expected_visual_description=expected_visual_description,
            history_item_id=queue_id,
        )
        self._items.append(item)
        return item

    def pop(self) -> Optional[QueuedPrompt]:
        """Remove and return the oldest queued prompt."""
        return self._items.popleft() if self._items else None

    @property
    def items(self) -> List[QueuedPrompt]:
        """Return a snapshot in display/processing order."""
        return list(self._items)

    def clear(self) -> List[QueuedPrompt]:
        """Remove all queued prompts and return the removed snapshot."""
        removed = list(self._items)
        self._items.clear()
        return removed

    def __len__(self) -> int:
        return len(self._items)
