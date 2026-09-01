import logging
from dataclasses import dataclass

from pymongo.errors import PyMongoError

from app.config import Settings
from app.metrics.registry import COUNTER_RESERVATION_CONTENTION, MetricsRegistry
from app.logging_config import log_event
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.enums import AgentState
from app.repositories.agent_repo import AgentRepository
from app.repositories.borrower_repo import BorrowerReleaseOutcome, BorrowerRepository
from app.state_machines.agent_sm import TransitionActor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReservationPair:
    campaign_id: str
    worker_id: str
    agent: Agent
    borrower: Borrower


class ReservationService:
    def __init__(
        self,
        agent_repository: AgentRepository,
        borrower_repository: BorrowerRepository,
        settings: Settings,
        registry: MetricsRegistry | None = None,
    ) -> None:
        self._agents = agent_repository
        self._borrowers = borrower_repository
        self._settings = settings
        self._registry = registry or MetricsRegistry()

    async def reserve_pair(self, campaign_id: str, worker_id: str) -> ReservationPair | None:
        agent = await self._claim_agent(campaign_id, worker_id)
        if agent is None:
            return None

        try:
            borrower = await self._claim_borrower(campaign_id, worker_id)
        except PyMongoError:
            await self._release_agent(agent.id, worker_id)
            raise

        if borrower is None:
            await self._release_agent(agent.id, worker_id)
            return None

        return ReservationPair(
            campaign_id=campaign_id,
            worker_id=worker_id,
            agent=agent,
            borrower=borrower,
        )

    async def release_pair(
        self,
        pair: ReservationPair,
        outcome: BorrowerReleaseOutcome,
    ) -> None:
        await self._release_agent(pair.agent.id, pair.worker_id)
        await self._borrowers.release_borrower(
            borrower_id=pair.borrower.id,
            worker_id=pair.worker_id,
            outcome=outcome,
            max_attempts=self._settings.MAX_CALL_ATTEMPTS,
            backoff_base_seconds=self._settings.RETRY_BACKOFF_BASE_SECONDS,
        )

    async def _claim_agent(self, campaign_id: str, worker_id: str) -> Agent | None:
        candidates = await self._agents.find_claimable_agents(campaign_id, needed=1)
        for candidate in candidates:
            agent = await self._agents.try_reserve_agent(
                campaign_id=campaign_id,
                agent_id=candidate.id,
                worker_id=worker_id,
                ttl_seconds=self._settings.RESERVATION_TTL_SECONDS,
            )
            if agent is not None:
                return agent
            self._registry.increment(COUNTER_RESERVATION_CONTENTION)
            log_event(
                logger,
                logging.DEBUG,
                "reservation_contention",
                "Another worker reserved this agent first",
                campaign_id=campaign_id,
                agent_id=candidate.id,
                worker_id=worker_id,
            )
        return None

    async def _claim_borrower(self, campaign_id: str, worker_id: str) -> Borrower | None:
        candidates = await self._borrowers.find_claimable_borrowers(campaign_id, needed=1)
        for candidate in candidates:
            borrower = await self._borrowers.try_reserve_borrower(
                campaign_id=campaign_id,
                borrower_id=candidate.id,
                worker_id=worker_id,
                ttl_seconds=self._settings.RESERVATION_TTL_SECONDS,
            )
            if borrower is not None:
                return borrower
            self._registry.increment(COUNTER_RESERVATION_CONTENTION)
            log_event(
                logger,
                logging.DEBUG,
                "reservation_contention",
                "Another worker reserved this borrower first",
                campaign_id=campaign_id,
                borrower_id=candidate.id,
                worker_id=worker_id,
            )
        return None

    async def _release_agent(self, agent_id: str, worker_id: str) -> None:
        await self._agents.release_agent(
            agent_id=agent_id,
            worker_id=worker_id,
            target_state=AgentState.AVAILABLE,
            actor=TransitionActor.ALLOCATOR,
        )
