import pytest

from app.models.enums import TERMINAL_CALL_STATES, AgentState, CallState
from app.state_machines.call_sm import (
    CALL_TRANSITIONS,
    EventApplicability,
    agent_state_for_call_state,
    can_transition,
    is_terminal,
    rank,
    should_apply_event,
)

EXPECTED_TRANSITIONS: dict[CallState, set[CallState]] = {
    CallState.QUEUED: {
        CallState.RESERVED,
        CallState.INITIATED,
        CallState.FAILED,
        CallState.CANCELLED,
    },
    CallState.RESERVED: {CallState.INITIATED, CallState.FAILED, CallState.CANCELLED},
    CallState.INITIATED: {
        CallState.RINGING,
        CallState.ANSWERED,
        CallState.CONNECTED,
        CallState.COMPLETED,
        CallState.FAILED,
        CallState.CANCELLED,
    },
    CallState.RINGING: {
        CallState.ANSWERED,
        CallState.CONNECTED,
        CallState.COMPLETED,
        CallState.FAILED,
        CallState.CANCELLED,
    },
    CallState.ANSWERED: {
        CallState.CONNECTED,
        CallState.COMPLETED,
        CallState.FAILED,
        CallState.CANCELLED,
    },
    CallState.CONNECTED: {CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED},
    CallState.COMPLETED: set(),
    CallState.FAILED: set(),
    CallState.CANCELLED: set(),
}


@pytest.mark.parametrize("current", list(CallState))
@pytest.mark.parametrize("target", list(CallState))
def test_transition_matrix_matches_the_documented_table(current, target):
    assert can_transition(current, target) is (target in EXPECTED_TRANSITIONS[current])


def test_every_call_state_appears_in_the_transition_table():
    assert set(CALL_TRANSITIONS) == set(CallState)


def test_terminal_states_reject_every_outgoing_transition():
    for state in TERMINAL_CALL_STATES:
        assert is_terminal(state)
        for target in CallState:
            assert not can_transition(state, target)


def test_non_terminal_states_are_not_terminal():
    for state in set(CallState) - TERMINAL_CALL_STATES:
        assert not is_terminal(state)


def test_every_valid_transition_moves_the_rank_forward():
    for current, targets in CALL_TRANSITIONS.items():
        for target in targets:
            assert rank(target) > rank(current)


def test_terminal_event_is_ignored():
    assert (
        should_apply_event(CallState.COMPLETED, CallState.ANSWERED)
        is EventApplicability.IGNORE_TERMINAL
    )


def test_out_of_order_sequence_leaves_the_call_terminal():
    outcomes = [
        should_apply_event(CallState.COMPLETED, CallState.ANSWERED),
        should_apply_event(CallState.COMPLETED, CallState.RINGING),
    ]

    assert outcomes == [EventApplicability.IGNORE_TERMINAL, EventApplicability.IGNORE_TERMINAL]


def test_late_event_is_stale():
    assert (
        should_apply_event(CallState.CONNECTED, CallState.RINGING)
        is EventApplicability.IGNORE_STALE
    )


def test_duplicate_event_is_stale():
    assert (
        should_apply_event(CallState.RINGING, CallState.RINGING) is EventApplicability.IGNORE_STALE
    )


def test_forward_skip_is_applied():
    assert (
        should_apply_event(CallState.INITIATED, CallState.ANSWERED) is EventApplicability.APPLY
    )


def test_progress_event_for_a_call_the_provider_never_received_is_invalid():
    assert (
        should_apply_event(CallState.QUEUED, CallState.ANSWERED)
        is EventApplicability.IGNORE_INVALID
    )


def test_normal_progression_is_applied():
    flow = [
        (CallState.QUEUED, CallState.RESERVED),
        (CallState.RESERVED, CallState.INITIATED),
        (CallState.INITIATED, CallState.RINGING),
        (CallState.RINGING, CallState.ANSWERED),
        (CallState.ANSWERED, CallState.CONNECTED),
        (CallState.CONNECTED, CallState.COMPLETED),
    ]

    for current, target in flow:
        assert should_apply_event(current, target) is EventApplicability.APPLY


@pytest.mark.parametrize("current", list(CallState))
@pytest.mark.parametrize("target", list(CallState))
def test_should_apply_event_agrees_with_the_transition_table(current, target):
    outcome = should_apply_event(current, target)

    if outcome is EventApplicability.APPLY:
        assert can_transition(current, target)
    else:
        assert not can_transition(current, target) or is_terminal(current)


def test_all_four_outcomes_are_reachable():
    outcomes = {
        should_apply_event(current, target)
        for current in CallState
        for target in CallState
    }

    assert outcomes == set(EventApplicability)


@pytest.mark.parametrize(
    "call_state,expected_agent_state",
    [
        (CallState.INITIATED, AgentState.DIALING),
        (CallState.RINGING, AgentState.DIALING),
        (CallState.ANSWERED, AgentState.CONNECTED),
        (CallState.CONNECTED, AgentState.CONNECTED),
        (CallState.COMPLETED, AgentState.WRAP_UP),
        (CallState.FAILED, AgentState.AVAILABLE),
        (CallState.CANCELLED, AgentState.AVAILABLE),
    ],
)
def test_implied_agent_state(call_state, expected_agent_state):
    assert agent_state_for_call_state(call_state) is expected_agent_state


@pytest.mark.parametrize("call_state", [CallState.QUEUED, CallState.RESERVED])
def test_call_states_before_dialing_imply_no_agent_state(call_state):
    assert agent_state_for_call_state(call_state) is None
