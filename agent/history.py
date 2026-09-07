"""Runtime session history data model.

Pure Python. Zero Blender (bpy) dependencies.
Maintains rich conversation and tool execution history during a runtime session.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional


class HistoryKind:
    """Categories of history items rendered in the UI."""

    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"
    SYSTEM = "SYSTEM"
    ERROR = "ERROR"


@dataclass
class HistoryItem:
    """A discrete item in the agent conversation and tool history."""

    item_id: str
    turn_id: str
    kind: str
    title: str
    status: str
    summary: str
    detail: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize item to a dictionary."""
        return {
            "detail": self.detail,
            "item_id": self.item_id,
            "kind": self.kind,
            "status": self.status,
            "summary": self.summary,
            "timestamp": round(self.timestamp, 4),
            "title": self.title,
            "turn_id": self.turn_id,
        }


class RuntimeHistory:
    """Manages the in-memory session history for the agent runtime."""

    def __init__(self, max_items: int = 100):
        self.max_items = max(1, max_items)
        self._items: List[HistoryItem] = []

    def add(
        self,
        item_id: str,
        turn_id: str,
        kind: str,
        title: str,
        status: str = "OK",
        summary: str = "",
        detail: str = "",
        timestamp: Optional[float] = None,
    ) -> HistoryItem:
        """Create and record a new history item with bounded capacity eviction."""
        item = HistoryItem(
            item_id=item_id,
            turn_id=turn_id,
            kind=kind,
            title=title,
            status=status,
            summary=summary or title,
            detail=detail or summary or title,
            timestamp=timestamp if timestamp is not None else time.time(),
        )
        self._items.append(item)
        if len(self._items) > self.max_items:
            self._items.pop(0)
        return item

    def get_by_id(self, item_id: str) -> Optional[HistoryItem]:
        """Lookup an item by its unique item_id."""
        for item in self._items:
            if item.item_id == item_id:
                return item
        return None

    def get_by_index(self, index: int) -> Optional[HistoryItem]:
        """Lookup an item by list index."""
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    @property
    def items(self) -> List[HistoryItem]:
        """Get a copy of all recorded history items."""
        return list(self._items)

    def clear(self) -> None:
        """Clear all session history items."""
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
