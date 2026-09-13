"""Agent state machine controlling execution lifecycle.

Zero Blender dependencies. Pure Python.
"""

from enum import Enum
from typing import Dict, Set


class AgentState(str, Enum):
    """Lifecycle states of the agent runtime."""

    IDLE = "IDLE"
    PROCESSING = "PROCESSING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTING_TOOL = "EXECUTING_TOOL"
    ERROR = "ERROR"


class InvalidStateTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""


class AgentStateMachine:
    """Deterministic finite state machine for agent turn lifecycle."""

    _ALLOWED_TRANSITIONS: Dict[AgentState, Set[AgentState]] = {
        AgentState.IDLE: {AgentState.PROCESSING},
        AgentState.PROCESSING: {
            AgentState.EXECUTING_TOOL,
            AgentState.PENDING_APPROVAL,
            AgentState.IDLE,
            AgentState.ERROR,
        },
        AgentState.PENDING_APPROVAL: {
            AgentState.EXECUTING_TOOL,
            AgentState.PROCESSING,
            AgentState.IDLE,
            AgentState.ERROR,
        },
        AgentState.EXECUTING_TOOL: {
            AgentState.PROCESSING,
            AgentState.ERROR,
        },
        AgentState.ERROR: {AgentState.IDLE},
    }

    def __init__(self, initial_state: AgentState = AgentState.IDLE):
        self._current_state: AgentState = initial_state

    @property
    def current_state(self) -> AgentState:
        """Get the current agent state."""
        return self._current_state

    def transition_to(self, target_state: AgentState) -> None:
        """Transition to target state if permitted by the transition graph.

        Args:
            target_state: The desired target AgentState.

        Raises:
            InvalidStateTransitionError: If the transition is illegal.
        """
        allowed = self._ALLOWED_TRANSITIONS.get(self._current_state, set())
        if target_state not in allowed:
            raise InvalidStateTransitionError(
                f"Illegal agent state transition from '{self._current_state.value}' "
                f"to '{target_state.value}'."
            )
        self._current_state = target_state

    def reset(self) -> None:
        """Safely reset the state machine back to IDLE."""
        self._current_state = AgentState.IDLE

    def __repr__(self) -> str:
        return f"<AgentStateMachine state={self._current_state.value}>"
