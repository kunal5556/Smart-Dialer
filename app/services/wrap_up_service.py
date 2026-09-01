from datetime import timedelta

from app.config import Settings
from app.models.agent import Agent
from app.repositories.agent_repo import AgentRepository

WRAP_UP_SWEEP_LIMIT = 500


class WrapUpService:
    def __init__(self, agent_repository: AgentRepository, settings: Settings) -> None:
        self._agents = agent_repository
        self._settings = settings

    async def find_finished_wrap_ups(
        self,
        campaign_id: str,
        limit: int = WRAP_UP_SWEEP_LIMIT,
    ) -> list[Agent]:
        cutoff = self._agents.now() - timedelta(seconds=self._settings.WRAP_UP_SECONDS)
        return await self._agents.find_expired_wrap_ups(
            older_than=cutoff,
            limit=limit,
            campaign_id=campaign_id,
        )
