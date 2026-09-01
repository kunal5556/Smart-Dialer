from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from app.config import Settings
from app.metrics.registry import (
    COUNTER_PROVIDER_FAILURES,
    COUNTER_RESERVATION_CONTENTION,
    COUNTER_RETRY_ATTEMPTS,
    MetricsRegistry,
)
from app.metrics.utilization import campaign_utilization
from app.models.base import utc_now
from app.models.campaign import Campaign
from app.models.enums import AgentState, CallState, SafetyVerdict
from app.repositories.agent_repo import AgentRepository
from app.repositories.call_repo import CallRepository
from app.repositories.decision_repo import DecisionRepository

ACTIVE_CALL_STATES = (
    CallState.RESERVED,
    CallState.INITIATED,
    CallState.RINGING,
    CallState.ANSWERED,
    CallState.CONNECTED,
)


@dataclass(frozen=True)
class CampaignMetrics:
    campaign_id: str
    dialing_mode: str
    campaign_status: str
    collected_at: datetime
    calls_initiated: int
    calls_connected: int
    calls_completed: int
    calls_failed: int
    calls_cancelled: int
    calls_ringing: int
    active_calls: int
    peak_concurrent_calls: int
    answer_rate: float | None
    average_talk_time_seconds: float
    average_setup_time_ms: float
    agent_states: dict[str, int]
    talk_utilization: float | None
    productive_utilization: float | None
    safety_verdicts: dict[str, int]
    progressive_fallbacks: int
    reservation_contention: int
    retry_attempts: int
    provider_failures: int
    counters: dict[str, int] = field(default_factory=dict)

    def to_document(self) -> dict:
        return asdict(self)


class CampaignMetricsCollector:
    def __init__(
        self,
        agent_repository: AgentRepository,
        call_repository: CallRepository,
        decision_repository: DecisionRepository,
        registry: MetricsRegistry,
        settings: Settings,
    ) -> None:
        self._agents = agent_repository
        self._calls = call_repository
        self._decisions = decision_repository
        self._registry = registry
        self._settings = settings
        self._peak_concurrent: dict[str, int] = {}

    async def collect(self, campaign: Campaign) -> CampaignMetrics:
        now = utc_now()
        call_counts = await self._calls.count_by_state(campaign.id)
        agent_counts = await self._agents.count_by_state(campaign.id)
        agents = await self._agents.find_for_campaign(campaign.id)
        utilization = campaign_utilization(agents)

        window_start = now - timedelta(seconds=self._settings.ANSWER_RATE_WINDOW_SECONDS)
        outcomes = await self._calls.outcome_counts_between(campaign.id, window_start, now)
        answer_rate = None
        if outcomes["total"]:
            answer_rate = outcomes["answered"] / outcomes["total"]

        active_calls = sum(call_counts[state] for state in ACTIVE_CALL_STATES)
        peak = max(self._peak_concurrent.get(campaign.id, 0), active_calls)
        self._peak_concurrent[campaign.id] = peak

        verdicts = await self._decisions.count_by_verdict(campaign.id)

        return CampaignMetrics(
            campaign_id=campaign.id,
            dialing_mode=campaign.dialing_mode.value,
            campaign_status=campaign.status.value,
            collected_at=now,
            calls_initiated=sum(
                call_counts[state] for state in CallState if state is not CallState.QUEUED
            ),
            calls_connected=call_counts[CallState.CONNECTED],
            calls_completed=call_counts[CallState.COMPLETED],
            calls_failed=call_counts[CallState.FAILED],
            calls_cancelled=call_counts[CallState.CANCELLED],
            calls_ringing=call_counts[CallState.RINGING],
            active_calls=active_calls,
            peak_concurrent_calls=peak,
            answer_rate=answer_rate,
            average_talk_time_seconds=await self._calls.average_talk_time_seconds(campaign.id),
            average_setup_time_ms=await self._calls.average_setup_time_ms(campaign.id),
            agent_states={state.value: agent_counts[state] for state in AgentState},
            talk_utilization=utilization.talk_utilization,
            productive_utilization=utilization.productive_utilization,
            safety_verdicts={
                verdict.value: verdicts.get(verdict, 0) for verdict in SafetyVerdict
            },
            progressive_fallbacks=verdicts.get(SafetyVerdict.FALLBACK_PROGRESSIVE, 0),
            reservation_contention=self._registry.value(COUNTER_RESERVATION_CONTENTION),
            retry_attempts=self._registry.value(COUNTER_RETRY_ATTEMPTS),
            provider_failures=self._registry.value(COUNTER_PROVIDER_FAILURES),
            counters=self._registry.snapshot(),
        )
