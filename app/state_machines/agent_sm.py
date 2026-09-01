from enum import Enum

from app.models.enums import AgentState
from app.state_machines.errors import InvalidStateTransition, UnauthorizedTransitionActor


class TransitionActor(str, Enum):
    ALLOCATOR = "ALLOCATOR"
    EVENT_PROCESSOR = "EVENT_PROCESSOR"
    RECOVERY = "RECOVERY"
    AGENT = "AGENT"
    WORKER_TIMER = "WORKER_TIMER"


AGENT_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.OFFLINE: frozenset({AgentState.AVAILABLE}),
    AgentState.AVAILABLE: frozenset(
        {AgentState.RESERVED, AgentState.PAUSED, AgentState.OFFLINE}
    ),
    AgentState.RESERVED: frozenset(
        {AgentState.DIALING, AgentState.AVAILABLE, AgentState.OFFLINE}
    ),
    AgentState.DIALING: frozenset(
        {AgentState.CONNECTED, AgentState.AVAILABLE, AgentState.OFFLINE}
    ),
    AgentState.CONNECTED: frozenset(
        {AgentState.WRAP_UP, AgentState.AVAILABLE, AgentState.OFFLINE}
    ),
    AgentState.WRAP_UP: frozenset(
        {AgentState.AVAILABLE, AgentState.PAUSED, AgentState.OFFLINE}
    ),
    AgentState.PAUSED: frozenset({AgentState.AVAILABLE, AgentState.OFFLINE}),
}


TRANSITION_ACTORS: dict[tuple[AgentState, AgentState], frozenset[TransitionActor]] = {
    (AgentState.OFFLINE, AgentState.AVAILABLE): frozenset({TransitionActor.AGENT}),
    (AgentState.AVAILABLE, AgentState.RESERVED): frozenset({TransitionActor.ALLOCATOR}),
    (AgentState.AVAILABLE, AgentState.PAUSED): frozenset({TransitionActor.AGENT}),
    (AgentState.AVAILABLE, AgentState.OFFLINE): frozenset(
        {TransitionActor.AGENT, TransitionActor.RECOVERY}
    ),
    (AgentState.RESERVED, AgentState.DIALING): frozenset({TransitionActor.ALLOCATOR}),
    (AgentState.RESERVED, AgentState.AVAILABLE): frozenset(
        {TransitionActor.ALLOCATOR, TransitionActor.RECOVERY}
    ),
    (AgentState.RESERVED, AgentState.OFFLINE): frozenset(
        {TransitionActor.AGENT, TransitionActor.RECOVERY}
    ),
    (AgentState.DIALING, AgentState.CONNECTED): frozenset({TransitionActor.EVENT_PROCESSOR}),
    (AgentState.DIALING, AgentState.AVAILABLE): frozenset(
        {TransitionActor.ALLOCATOR, TransitionActor.EVENT_PROCESSOR, TransitionActor.RECOVERY}
    ),
    (AgentState.DIALING, AgentState.OFFLINE): frozenset(
        {TransitionActor.AGENT, TransitionActor.RECOVERY}
    ),
    (AgentState.CONNECTED, AgentState.WRAP_UP): frozenset({TransitionActor.EVENT_PROCESSOR}),
    (AgentState.CONNECTED, AgentState.AVAILABLE): frozenset(
        {TransitionActor.EVENT_PROCESSOR, TransitionActor.RECOVERY}
    ),
    (AgentState.CONNECTED, AgentState.OFFLINE): frozenset(
        {TransitionActor.AGENT, TransitionActor.RECOVERY}
    ),
    (AgentState.WRAP_UP, AgentState.AVAILABLE): frozenset(
        {TransitionActor.WORKER_TIMER, TransitionActor.RECOVERY}
    ),
    (AgentState.WRAP_UP, AgentState.PAUSED): frozenset({TransitionActor.AGENT}),
    (AgentState.WRAP_UP, AgentState.OFFLINE): frozenset(
        {TransitionActor.AGENT, TransitionActor.RECOVERY}
    ),
    (AgentState.PAUSED, AgentState.AVAILABLE): frozenset({TransitionActor.AGENT}),
    (AgentState.PAUSED, AgentState.OFFLINE): frozenset(
        {TransitionActor.AGENT, TransitionActor.RECOVERY}
    ),
}


BUSY_AGENT_STATES: frozenset[AgentState] = frozenset(
    {AgentState.RESERVED, AgentState.DIALING, AgentState.CONNECTED, AgentState.WRAP_UP}
)


def can_transition(current: AgentState, target: AgentState) -> bool:
    return target in AGENT_TRANSITIONS[current]


def is_actor_allowed(current: AgentState, target: AgentState, actor: TransitionActor) -> bool:
    return actor in TRANSITION_ACTORS.get((current, target), frozenset())


def validate_transition(current: AgentState, target: AgentState, actor: TransitionActor) -> None:
    if not can_transition(current, target):
        raise InvalidStateTransition(current.value, target.value, actor.value)
    if not is_actor_allowed(current, target, actor):
        raise UnauthorizedTransitionActor(current.value, target.value, actor.value)


def allowed_sources(target: AgentState, actor: TransitionActor) -> frozenset[AgentState]:
    return frozenset(
        state
        for state in AgentState
        if can_transition(state, target) and is_actor_allowed(state, target, actor)
    )


def is_claimable(state: AgentState) -> bool:
    return state is AgentState.AVAILABLE


def counts_as_busy(state: AgentState) -> bool:
    return state in BUSY_AGENT_STATES
