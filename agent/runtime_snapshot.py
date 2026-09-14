"""Immutable, UI-neutral projection of AgentRuntime state."""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Read-only runtime view shared by Blender UI surfaces."""

    state: str
    current_turn_id: Optional[str]
    queued_count: int
    streaming_text: str
    last_response_text: str
    last_plan_summary: Optional[Dict[str, Any]]
    pending_approval: Optional[Dict[str, Any]]
    history: Tuple[Dict[str, Any], ...]
