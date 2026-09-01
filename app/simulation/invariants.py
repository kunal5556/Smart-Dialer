from dataclasses import dataclass, field
from datetime import timedelta

from app.config import Settings
from app.models.base import utc_now
from app.models.enums import AgentState, CallState
from app.repositories.agent_repo import AgentRepository
from app.repositories.call_repo import CallRepository

NON_TERMINAL_CALL_STATES = [
    CallState.QUEUED.value,
    CallState.RESERVED.value,
    CallState.INITIATED.value,
    CallState.RINGING.value,
    CallState.ANSWERED.value,
    CallState.CONNECTED.value,
]

INVARIANT_AGENT_DOUBLE_BOOKED = "agent_bound_to_two_active_calls"
INVARIANT_BORROWER_DOUBLE_BOOKED = "borrower_in_two_active_calls"
INVARIANT_CALLS_EXCEED_AGENTS = "agent_bound_calls_exceed_usable_agents"
INVARIANT_STALE_CALL = "non_terminal_call_older_than_stale_timeout"
INVARIANT_STUCK_RESERVATION = "agent_left_reserved"


@dataclass
class InvariantViolation:
    name: str
    detail: str
    offending_ids: list[str] = field(default_factory=list)


class InvariantChecker:
    def __init__(
        self,
        agent_repository: AgentRepository,
        call_repository: CallRepository,
        settings: Settings,
    ) -> None:
        self._agents = agent_repository
        self._calls = call_repository
        self._settings = settings

    async def check(self, campaign_id: str, final: bool = False) -> list[InvariantViolation]:
        violations: list[InvariantViolation] = []
        active_calls = await self._calls.find_active(campaign_id)
        agent_counts = await self._agents.count_by_state(campaign_id)

        violations.extend(self._check_duplicate_bindings(active_calls))
        violations.extend(self._check_capacity(active_calls, agent_counts))
        if final:
            violations.extend(await self._check_final_state(campaign_id, agent_counts))
        return violations

    def _check_duplicate_bindings(self, active_calls: list) -> list[InvariantViolation]:
        violations = []
        for name, key in (
            (INVARIANT_AGENT_DOUBLE_BOOKED, "agent_id"),
            (INVARIANT_BORROWER_DOUBLE_BOOKED, "borrower_id"),
        ):
            seen: dict[str, str] = {}
            offenders: list[str] = []
            for call in active_calls:
                identifier = getattr(call, key)
                if identifier in seen:
                    offenders.append(f"{identifier} in {seen[identifier]} and {call.id}")
                seen[identifier] = call.id
            if offenders:
                violations.append(
                    InvariantViolation(
                        name=name,
                        detail=f"{len(offenders)} duplicate bindings detected",
                        offending_ids=offenders,
                    )
                )
        return violations

    def _check_capacity(
        self,
        active_calls: list,
        agent_counts: dict[AgentState, int],
    ) -> list[InvariantViolation]:
        usable_agents = sum(
            agent_counts[state]
            for state in AgentState
            if state not in (AgentState.OFFLINE, AgentState.PAUSED)
        )
        if len(active_calls) <= usable_agents:
            return []
        return [
            InvariantViolation(
                name=INVARIANT_CALLS_EXCEED_AGENTS,
                detail=f"{len(active_calls)} agent-bound calls for {usable_agents} usable agents",
                offending_ids=[call.id for call in active_calls],
            )
        ]

    async def _check_final_state(
        self,
        campaign_id: str,
        agent_counts: dict[AgentState, int],
    ) -> list[InvariantViolation]:
        violations = []
        cutoff = utc_now() - timedelta(seconds=self._settings.CALL_STALE_TIMEOUT_SECONDS)
        stale = await self._calls.find_stale_calls(cutoff, limit=50)
        stale = [call for call in stale if call.campaign_id == campaign_id]
        if stale:
            violations.append(
                InvariantViolation(
                    name=INVARIANT_STALE_CALL,
                    detail=f"{len(stale)} calls stayed non-terminal past the stale timeout",
                    offending_ids=[call.id for call in stale],
                )
            )
        if agent_counts[AgentState.RESERVED]:
            violations.append(
                InvariantViolation(
                    name=INVARIANT_STUCK_RESERVATION,
                    detail=f"{agent_counts[AgentState.RESERVED]} agents left RESERVED",
                )
            )
        return violations
