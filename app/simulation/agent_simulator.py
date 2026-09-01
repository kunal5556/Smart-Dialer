import asyncio
import logging

from app.logging_config import log_event
from app.models.enums import AgentState
from app.repositories.agent_repo import AgentRepository
from app.simulation.config import SimulationConfig
from app.state_machines.agent_sm import TransitionActor

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 1.0


class AgentSimulator:
    def __init__(
        self,
        agent_repository: AgentRepository,
        campaign_id: str,
        config: SimulationConfig,
    ) -> None:
        self._agents = agent_repository
        self._campaign_id = campaign_id
        self._config = config
        self._task: asyncio.Task | None = None

    async def log_everyone_in(self) -> int:
        logged_in = 0
        for agent in await self._agents.find_for_campaign(self._campaign_id):
            if agent.state is not AgentState.OFFLINE:
                continue
            updated = await self._agents.transition_agent(
                agent_id=agent.id,
                from_state=AgentState.OFFLINE,
                to_state=AgentState.AVAILABLE,
                actor=TransitionActor.AGENT,
                expected_version=agent.state_version,
            )
            if updated is not None:
                await self._agents.heartbeat(agent.id)
                logged_in += 1
        return logged_in

    async def take_agents_offline(self, count: int) -> int:
        removed = 0
        for agent in await self._agents.find_for_campaign(self._campaign_id):
            if removed >= count:
                break
            if agent.state is not AgentState.AVAILABLE:
                continue
            updated = await self._agents.transition_agent(
                agent_id=agent.id,
                from_state=AgentState.AVAILABLE,
                to_state=AgentState.OFFLINE,
                actor=TransitionActor.AGENT,
                expected_version=agent.state_version,
            )
            if updated is not None:
                removed += 1
        log_event(
            logger,
            logging.WARNING,
            "simulated_agents_left",
            f"{removed} simulated agents went offline",
            campaign_id=self._campaign_id,
        )
        return removed

    def start_heartbeats(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _heartbeat_loop(self) -> None:
        interval = self._config.scaled(HEARTBEAT_INTERVAL_SECONDS)
        while True:
            for agent in await self._agents.find_for_campaign(self._campaign_id):
                if agent.state is not AgentState.OFFLINE:
                    await self._agents.heartbeat(agent.id)
            await asyncio.sleep(interval)
