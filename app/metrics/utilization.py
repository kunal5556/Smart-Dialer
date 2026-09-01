from dataclasses import dataclass

from app.models.agent import Agent
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


def agent_utilization(agent: Agent) -> AgentUtilization:
    connected = agent.connected_time_ms
    productive = agent.connected_time_ms + agent.wrap_up_time_ms
    counted = agent.busy_time_ms + agent.available_time_ms

    return AgentUtilization(
        agent_id=agent.id,
        connected_time_ms=connected,
        productive_time_ms=productive,
        counted_time_ms=counted,
        talk_utilization=_ratio(connected, counted),
        productive_utilization=_ratio(productive, counted),
    )


def campaign_utilization(agents: list[Agent]) -> CampaignUtilization:
    connected = sum(agent.connected_time_ms for agent in agents)
    productive = sum(agent.connected_time_ms + agent.wrap_up_time_ms for agent in agents)
    counted = sum(agent.busy_time_ms + agent.available_time_ms for agent in agents)

    return CampaignUtilization(
        connected_time_ms=connected,
        productive_time_ms=productive,
        counted_time_ms=counted,
        talk_utilization=_ratio(connected, counted),
        productive_utilization=_ratio(productive, counted),
        agents_counted=len(agents),
    )
