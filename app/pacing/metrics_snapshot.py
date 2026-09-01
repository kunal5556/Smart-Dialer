from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings
from app.models.base import utc_now
from app.models.campaign import Campaign
from app.models.enums import AgentState, CallState, DialingMode, ProviderHealthStatus
from app.pacing.answer_rate import observed_answer_rate
from app.repositories.agent_repo import AgentRepository
from app.repositories.call_repo import CallRepository
from app.services.provider_health import ProviderHealthManager


@dataclass(frozen=True)
class PacingSnapshot:
    campaign_id: str
    mode: DialingMode
    available_agents: int
    reserved_agents: int
    dialing_agents: int
    connected_agents: int
    wrap_up_agents: int
    long_connected_agents: int
    previous_available_agents: int
    ringing_calls: int
    initiated_calls: int
    active_calls: int
    recent_answer_rate: float | None
    previous_answer_rate: float | None
    baseline_answer_rate: float
    avg_talk_time_seconds: float
    avg_setup_time_ms: float
    provider_status: ProviderHealthStatus
    health_factor: float
    captured_at: datetime

    @property
    def low_confidence(self) -> bool:
        return self.recent_answer_rate is None


class MetricsSnapshotBuilder:
    def __init__(
        self,
        agent_repository: AgentRepository,
        call_repository: CallRepository,
        health_manager: ProviderHealthManager,
        settings: Settings,
    ) -> None:
        self._agents = agent_repository
        self._calls = call_repository
        self._health = health_manager
        self._settings = settings
        self._previous_available: dict[str, int] = {}

    async def build_snapshot(self, campaign: Campaign) -> PacingSnapshot:
        now = utc_now()
        agent_counts = await self._agents.count_by_state(campaign.id)
        call_counts = await self._calls.count_by_state(campaign.id)
        avg_talk_time = await self._calls.average_talk_time_seconds(campaign.id)
        avg_setup_time = await self._calls.average_setup_time_ms(campaign.id)

        window = timedelta(seconds=self._settings.ANSWER_RATE_WINDOW_SECONDS)
        recent = await self._calls.outcome_counts_between(campaign.id, now - window, now)
        previous = await self._calls.outcome_counts_between(
            campaign.id, now - window * 2, now - window
        )

        long_connected = 0
        if avg_talk_time > 0:
            long_connected = await self._agents.count_connected_longer_than(
                campaign.id, now - timedelta(seconds=avg_talk_time)
            )

        available_agents = agent_counts[AgentState.AVAILABLE]
        previous_available = self._previous_available.get(campaign.id, available_agents)
        self._previous_available[campaign.id] = available_agents

        health = self._health.get_health(campaign.provider_name)

        return PacingSnapshot(
            campaign_id=campaign.id,
            mode=campaign.dialing_mode,
            available_agents=available_agents,
            reserved_agents=agent_counts[AgentState.RESERVED],
            dialing_agents=agent_counts[AgentState.DIALING],
            connected_agents=agent_counts[AgentState.CONNECTED],
            wrap_up_agents=agent_counts[AgentState.WRAP_UP],
            long_connected_agents=long_connected,
            previous_available_agents=previous_available,
            ringing_calls=call_counts[CallState.RINGING],
            initiated_calls=call_counts[CallState.INITIATED],
            active_calls=call_counts[CallState.RINGING] + call_counts[CallState.INITIATED],
            recent_answer_rate=observed_answer_rate(recent["answered"], recent["total"]),
            previous_answer_rate=observed_answer_rate(
                previous["answered"], previous["total"]
            ),
            baseline_answer_rate=campaign.pacing_config.baseline_answer_rate,
            avg_talk_time_seconds=avg_talk_time,
            avg_setup_time_ms=avg_setup_time,
            provider_status=health.status,
            health_factor=self._health.health_factor(campaign.provider_name),
            captured_at=now,
        )
