import pytest

from app.models.enums import AgentState
from app.state_machines.agent_sm import (
    AGENT_TRANSITIONS,
    TRANSITION_ACTORS,
    TransitionActor,
    allowed_sources,
    can_transition,
    counts_as_busy,
    is_actor_allowed,
    is_claimable,
    validate_transition,
)
from app.state_machines.errors import InvalidStateTransition, UnauthorizedTransitionActor

EXPECTED_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.OFFLINE: {AgentState.AVAILABLE},
    AgentState.AVAILABLE: {AgentState.RESERVED, AgentState.PAUSED, AgentState.OFFLINE},
    AgentState.RESERVED: {AgentState.DIALING, AgentState.AVAILABLE, AgentState.OFFLINE},
    AgentState.DIALING: {AgentState.CONNECTED, AgentState.AVAILABLE, AgentState.OFFLINE},
    AgentState.CONNECTED: {AgentState.WRAP_UP, AgentState.AVAILABLE, AgentState.OFFLINE},
    AgentState.WRAP_UP: {AgentState.AVAILABLE, AgentState.PAUSED, AgentState.OFFLINE},
    AgentState.PAUSED: {AgentState.AVAILABLE, AgentState.OFFLINE},
}


@pytest.mark.parametrize("current", list(AgentState))
@pytest.mark.parametrize("target", list(AgentState))
def test_transition_matrix_matches_the_documented_table(current, target):
    assert can_transition(current, target) is (target in EXPECTED_TRANSITIONS[current])


def test_every_agent_state_appears_in_the_transition_table():
    assert set(AGENT_TRANSITIONS) == set(AgentState)


def test_no_self_transitions_are_allowed():
    for state in AgentState:
        assert not can_transition(state, state)


@pytest.mark.parametrize(
    "current,target",
    [
        (AgentState.AVAILABLE, AgentState.CONNECTED),
        (AgentState.RESERVED, AgentState.CONNECTED),
        (AgentState.OFFLINE, AgentState.RESERVED),
        (AgentState.WRAP_UP, AgentState.DIALING),
        (AgentState.PAUSED, AgentState.RESERVED),
    ],
)
def test_documented_invalid_transitions_raise(current, target):
    with pytest.raises(InvalidStateTransition) as error:
        validate_transition(current, target, TransitionActor.ALLOCATOR)

    assert error.value.current == current.value
    assert error.value.target == target.value
    assert error.value.actor == TransitionActor.ALLOCATOR.value


def test_event_processor_cannot_reserve_an_agent():
    with pytest.raises(UnauthorizedTransitionActor) as error:
        validate_transition(
            AgentState.AVAILABLE, AgentState.RESERVED, TransitionActor.EVENT_PROCESSOR
        )

    assert error.value.actor == TransitionActor.EVENT_PROCESSOR.value


def test_allocator_cannot_connect_a_call():
    with pytest.raises(UnauthorizedTransitionActor):
        validate_transition(AgentState.DIALING, AgentState.CONNECTED, TransitionActor.ALLOCATOR)


def test_allocator_may_reserve_and_dial():
    validate_transition(AgentState.AVAILABLE, AgentState.RESERVED, TransitionActor.ALLOCATOR)
    validate_transition(AgentState.RESERVED, AgentState.DIALING, TransitionActor.ALLOCATOR)


def test_recovery_may_release_a_stale_reservation():
    validate_transition(AgentState.RESERVED, AgentState.AVAILABLE, TransitionActor.RECOVERY)


def test_every_valid_transition_has_at_least_one_actor():
    for current, targets in AGENT_TRANSITIONS.items():
        for target in targets:
            assert TRANSITION_ACTORS.get((current, target)), f"{current} -> {target} has no actor"


def test_actor_table_contains_no_invalid_transitions():
    for current, target in TRANSITION_ACTORS:
        assert can_transition(current, target)


def test_allowed_sources_for_recovery_release():
    assert allowed_sources(AgentState.AVAILABLE, TransitionActor.RECOVERY) == frozenset(
        {
            AgentState.RESERVED,
            AgentState.DIALING,
            AgentState.CONNECTED,
            AgentState.WRAP_UP,
        }
    )


def test_allowed_sources_for_allocator_reservation():
    assert allowed_sources(AgentState.RESERVED, TransitionActor.ALLOCATOR) == frozenset(
        {AgentState.AVAILABLE}
    )


def test_is_actor_allowed_returns_false_for_an_invalid_transition():
    assert not is_actor_allowed(AgentState.OFFLINE, AgentState.RESERVED, TransitionActor.ALLOCATOR)


def test_only_available_agents_are_claimable():
    for state in AgentState:
        assert is_claimable(state) is (state is AgentState.AVAILABLE)


def test_busy_states_are_the_states_bound_to_work():
    busy = {state for state in AgentState if counts_as_busy(state)}

    assert busy == {
        AgentState.RESERVED,
        AgentState.DIALING,
        AgentState.CONNECTED,
        AgentState.WRAP_UP,
    }
