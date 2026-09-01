from enum import Enum

from app.models.enums import CALL_STATE_RANK, TERMINAL_CALL_STATES, AgentState, CallState


class EventApplicability(str, Enum):
    APPLY = "APPLY"
    IGNORE_TERMINAL = "IGNORE_TERMINAL"
    IGNORE_STALE = "IGNORE_STALE"
    IGNORE_INVALID = "IGNORE_INVALID"


CALL_TRANSITIONS: dict[CallState, frozenset[CallState]] = {
    CallState.QUEUED: frozenset(
        {CallState.RESERVED, CallState.INITIATED, CallState.FAILED, CallState.CANCELLED}
    ),
    CallState.RESERVED: frozenset(
        {CallState.INITIATED, CallState.FAILED, CallState.CANCELLED}
    ),
    CallState.INITIATED: frozenset(
        {
            CallState.RINGING,
            CallState.ANSWERED,
            CallState.CONNECTED,
            CallState.COMPLETED,
            CallState.FAILED,
            CallState.CANCELLED,
        }
    ),
    CallState.RINGING: frozenset(
        {
            CallState.ANSWERED,
            CallState.CONNECTED,
            CallState.COMPLETED,
            CallState.FAILED,
            CallState.CANCELLED,
        }
    ),
    CallState.ANSWERED: frozenset(
        {CallState.CONNECTED, CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED}
    ),
    CallState.CONNECTED: frozenset(
        {CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED}
    ),
    CallState.COMPLETED: frozenset(),
    CallState.FAILED: frozenset(),
    CallState.CANCELLED: frozenset(),
}


AGENT_STATE_FOR_CALL_STATE: dict[CallState, AgentState] = {
    CallState.INITIATED: AgentState.DIALING,
    CallState.RINGING: AgentState.DIALING,
    CallState.ANSWERED: AgentState.CONNECTED,
    CallState.CONNECTED: AgentState.CONNECTED,
    CallState.COMPLETED: AgentState.WRAP_UP,
    CallState.FAILED: AgentState.AVAILABLE,
    CallState.CANCELLED: AgentState.AVAILABLE,
}


def rank(state: CallState) -> int:
    return CALL_STATE_RANK[state]


def is_terminal(state: CallState) -> bool:
    return state in TERMINAL_CALL_STATES


def can_transition(current: CallState, target: CallState) -> bool:
    return target in CALL_TRANSITIONS[current]


def should_apply_event(current_state: CallState, target_state: CallState) -> EventApplicability:
    if is_terminal(current_state):
        return EventApplicability.IGNORE_TERMINAL
    if rank(target_state) <= rank(current_state):
        return EventApplicability.IGNORE_STALE
    if not can_transition(current_state, target_state):
        return EventApplicability.IGNORE_INVALID
    return EventApplicability.APPLY


def agent_state_for_call_state(call_state: CallState) -> AgentState | None:
    return AGENT_STATE_FOR_CALL_STATE.get(call_state)
