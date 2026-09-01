from dataclasses import dataclass
from datetime import datetime

from app.models.agent import Agent
from app.models.base import utc_now
from app.models.enums import AgentState

UTILIZATION_DENOMINATOR_STATES = frozenset(
    {
        AgentState.AVAILABLE,
        AgentState.RESERVED,
        AgentState.DIALING,
        AgentState.CONNECTED,
        AgentState.WRAP_UP,
    }
)


@dataclass(frozen=True)
class AgentUtilization:
    agent_id: str
    connected_time_ms: int
    productive_time_ms: int
    counted_time_ms: int
    talk_utilization: float | None
    productive_utilization: float | None


@dataclass(frozen=True)
class CampaignUtilization:
    connected_time_ms: int
    productive_time_ms: int
    counted_time_ms: int
    talk_utilization: float | None
    productive_utilization: float | None
    agents_counted: int


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def elapsed_in_current_state_ms(agent: Agent, now: datetime) -> int:
    if agent.state not in UTILIZATION_DENOMINATOR_STATES:
        return 0
    return max(0, int((now - agent.state_changed_at).total_seconds() * 1000))


def _live_totals(agent: Agent, now: datetime) -> tuple[int, int, int]:
    elapsed = elapsed_in_current_state_ms(agent, now)

    connected = agent.connected_time_ms
    wrap_up = agent.wrap_up_time_ms
    counted = agent.busy_time_ms + agent.available_time_ms + elapsed

    if agent.state is AgentState.CONNECTED:
        connected += elapsed
    elif agent.state is AgentState.WRAP_UP:
        wrap_up += elapsed

    return connected, connected + wrap_up, counted


def agent_utilization(agent: Agent, now: datetime | None = None) -> AgentUtilization:
    connected, productive, counted = _live_totals(agent, now or utc_now())

    return AgentUtilization(
        agent_id=agent.id,
        connected_time_ms=connected,
        productive_time_ms=productive,
        counted_time_ms=counted,
        talk_utilization=_ratio(connected, counted),
        productive_utilization=_ratio(productive, counted),
    )


def campaign_utilization(
    agents: list[Agent],
    now: datetime | None = None,
) -> CampaignUtilization:
    moment = now or utc_now()
    totals = [_live_totals(agent, moment) for agent in agents]

    connected = sum(item[0] for item in totals)
    productive = sum(item[1] for item in totals)
    counted = sum(item[2] for item in totals)

    return CampaignUtilization(
        connected_time_ms=connected,
        productive_time_ms=productive,
        counted_time_ms=counted,
        talk_utilization=_ratio(connected, counted),
        productive_utilization=_ratio(productive, counted),
        agents_counted=len(agents),
    )
