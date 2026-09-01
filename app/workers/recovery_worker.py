import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import Settings
from app.logging_config import log_event
from app.models.agent import Agent
from app.models.base import utc_now
from app.models.call import Call
from app.models.enums import AgentState, CallState
from app.providers.base import ProviderCallStatus
from app.providers.errors import ProviderError
from app.providers.registry import ProviderRegistry
from app.repositories.agent_repo import AgentRepository
from app.repositories.borrower_repo import BorrowerRepository
from app.repositories.call_repo import CallRepository
from app.services.retry_service import RetryService
from app.state_machines.agent_sm import TransitionActor

logger = logging.getLogger(__name__)

PROVIDER_STATUS_TO_CALL_STATE: dict[ProviderCallStatus, CallState] = {
    ProviderCallStatus.COMPLETED: CallState.COMPLETED,
    ProviderCallStatus.FAILED: CallState.FAILED,
}


@dataclass
class RecoverySweepCounts:
    expired_agent_leases: int = 0
    expired_borrower_leases: int = 0
    orphaned_calls: int = 0
    heartbeat_timeouts: int = 0
    stuck_wrap_ups: int = 0
    errors: list[str] = field(default_factory=list)


class RecoveryWorker:
    def __init__(
        self,
        agent_repository: AgentRepository,
        borrower_repository: BorrowerRepository,
        call_repository: CallRepository,
        provider_registry: ProviderRegistry,
        retry_service: RetryService,
        settings: Settings,
    ) -> None:
        self._agents = agent_repository
        self._borrowers = borrower_repository
        self._calls = call_repository
        self._providers = provider_registry
        self._retries = retry_service
        self._settings = settings
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.run_sweeps()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log_event(
                    logger,
                    logging.ERROR,
                    "recovery_loop_failed",
                    f"Recovery loop raised an error, continuing: {error}",
                )
            await asyncio.sleep(self._settings.RECOVERY_TICK_SECONDS)

    async def run_sweeps(self) -> RecoverySweepCounts:
        now = utc_now()
        limit = self._settings.RECOVERY_SWEEP_LIMIT
        counts = RecoverySweepCounts()

        sweeps = (
            ("expired_agent_leases", self._sweep_expired_agent_leases),
            ("expired_borrower_leases", self._sweep_expired_borrower_leases),
            ("orphaned_calls", self._sweep_orphaned_calls),
            ("heartbeat_timeouts", self._sweep_heartbeat_timeouts),
            ("stuck_wrap_ups", self._sweep_stuck_wrap_ups),
        )

        for name, sweep in sweeps:
            try:
                setattr(counts, name, await sweep(now, limit))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                counts.errors.append(name)
                log_event(
                    logger,
                    logging.ERROR,
                    "recovery_sweep_failed",
                    f"Recovery sweep {name} failed, continuing with the rest: {error}",
                )

        self._log_summary(counts)
        return counts

    async def _sweep_expired_agent_leases(self, now: datetime, limit: int) -> int:
        recovered = 0
        for agent in await self._agents.find_expired_agent_leases(now, limit):
            await self._cancel_bound_call(agent, reason="agent_lease_expired")
            released = await self._agents.release_expired_agent_lease(agent, now)
            if released is not None:
                recovered += 1
                log_event(
                    logger,
                    logging.WARNING,
                    "agent_lease_reclaimed",
                    "Reclaimed an agent whose worker lease expired",
                    campaign_id=agent.campaign_id,
                    agent_id=agent.id,
                    worker_id=agent.reserved_by,
                )
        return recovered

    async def _sweep_expired_borrower_leases(self, now: datetime, limit: int) -> int:
        reclaimed = await self._borrowers.reclaim_expired_borrower_leases(now, limit)
        for borrower in reclaimed:
            log_event(
                logger,
                logging.WARNING,
                "borrower_lease_reclaimed",
                "Reclaimed a borrower whose worker lease expired",
                campaign_id=borrower.campaign_id,
                borrower_id=borrower.id,
            )
        return len(reclaimed)

    async def _sweep_orphaned_calls(self, now: datetime, limit: int) -> int:
        cutoff = now - timedelta(seconds=self._settings.CALL_STALE_TIMEOUT_SECONDS)
        recovered = 0
        for call in await self._calls.find_stale_calls(cutoff, limit):
            target_state = await self._reconcile_with_provider(call)
            finished = await self._calls.transition_call(
                call_id=call.id,
                target_state=target_state,
                failure_reason="orphaned" if target_state is CallState.FAILED else None,
            )
            if finished is None:
                continue
            recovered += 1
            await self._release_parties(finished)
            log_event(
                logger,
                logging.WARNING,
                "orphaned_call_reconciled",
                f"Stale call reconciled to {target_state.value}",
                campaign_id=call.campaign_id,
                call_id=call.id,
            )
        return recovered

    async def _sweep_heartbeat_timeouts(self, now: datetime, limit: int) -> int:
        cutoff = now - timedelta(seconds=self._settings.AGENT_HEARTBEAT_TIMEOUT_SECONDS)
        logged_out = 0
        for agent in await self._agents.find_heartbeat_expired(cutoff, limit):
            await self._cancel_bound_call(agent, reason="agent_disappeared")
            updated = await self._agents.transition_agent(
                agent_id=agent.id,
                from_state=agent.state,
                to_state=AgentState.OFFLINE,
                actor=TransitionActor.RECOVERY,
                expected_version=agent.state_version,
            )
            if updated is None:
                continue
            logged_out += 1
            log_event(
                logger,
                logging.WARNING,
                "agent_heartbeat_timeout",
                "Agent stopped sending heartbeats and was taken offline",
                campaign_id=agent.campaign_id,
                agent_id=agent.id,
            )
        return logged_out

    async def _sweep_stuck_wrap_ups(self, now: datetime, limit: int) -> int:
        grace = self._settings.WRAP_UP_SECONDS * 2
        cutoff = now - timedelta(seconds=self._settings.WRAP_UP_SECONDS + grace)
        released = 0
        for agent in await self._agents.find_expired_wrap_ups(cutoff, limit):
            updated = await self._agents.transition_agent(
                agent_id=agent.id,
                from_state=AgentState.WRAP_UP,
                to_state=AgentState.AVAILABLE,
                actor=TransitionActor.RECOVERY,
                expected_version=agent.state_version,
            )
            if updated is not None:
                released += 1
        return released

    async def _reconcile_with_provider(self, call: Call) -> CallState:
        if call.provider_call_id is None:
            return CallState.FAILED
        try:
            provider = self._providers.get(call.provider_name)
            status = await provider.get_call_status(call.provider_call_id)
        except ProviderError:
            return CallState.FAILED
        return PROVIDER_STATUS_TO_CALL_STATE.get(status, CallState.FAILED)

    async def _cancel_bound_call(self, agent: Agent, reason: str) -> None:
        if agent.current_call_id is None:
            return
        call = await self._calls.find_by_id(agent.current_call_id)
        if call is None or call.terminal:
            return
        cancelled = await self._calls.transition_call(
            call_id=call.id,
            target_state=CallState.CANCELLED,
            failure_reason=reason,
        )
        if cancelled is not None:
            await self._release_borrower(cancelled)

    async def _release_parties(self, call: Call) -> None:
        await self._agents.release_agent(
            agent_id=call.agent_id,
            worker_id=call.created_by_worker,
            target_state=AgentState.AVAILABLE,
            actor=TransitionActor.RECOVERY,
        )
        await self._release_borrower(call)

    async def _release_borrower(self, call: Call) -> None:
        outcome = self._retries.outcome_for_terminal_call(answered=False)
        await self._borrowers.release_borrower(
            borrower_id=call.borrower_id,
            worker_id=call.created_by_worker,
            outcome=outcome,
            max_attempts=self._settings.MAX_CALL_ATTEMPTS,
            backoff_base_seconds=self._settings.RETRY_BACKOFF_BASE_SECONDS,
        )

    def _log_summary(self, counts: RecoverySweepCounts) -> None:
        total = (
            counts.expired_agent_leases
            + counts.expired_borrower_leases
            + counts.orphaned_calls
            + counts.heartbeat_timeouts
            + counts.stuck_wrap_ups
        )
        if total == 0 and not counts.errors:
            return
        log_event(
            logger,
            logging.INFO,
            "recovery_sweep_completed",
            f"Recovery reclaimed {counts.expired_agent_leases} agent leases, "
            f"{counts.expired_borrower_leases} borrower leases, "
            f"{counts.orphaned_calls} orphaned calls, "
            f"{counts.heartbeat_timeouts} disappeared agents, "
            f"{counts.stuck_wrap_ups} stuck wrap-ups",
        )
