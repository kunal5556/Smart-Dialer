import logging
from dataclasses import dataclass

from app.config import Settings
from app.logging_config import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AvailabilityDrop:
    campaign_id: str
    previous_available: int
    current_available: int
    drop_ratio: float


class AgentAvailabilityTracker:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._previous_available: dict[str, int] = {}

    def record_and_detect(self, campaign_id: str, available_agents: int) -> AvailabilityDrop | None:
        previous = self._previous_available.get(campaign_id)
        self._previous_available[campaign_id] = available_agents

        if not previous or available_agents >= previous:
            return None

        drop_ratio = (previous - available_agents) / previous
        if drop_ratio <= self._settings.AVAILABILITY_DROP_THRESHOLD:
            return None

        drop = AvailabilityDrop(
            campaign_id=campaign_id,
            previous_available=previous,
            current_available=available_agents,
            drop_ratio=drop_ratio,
        )
        log_event(
            logger,
            logging.WARNING,
            "agent_availability_drop",
            f"Available agents fell from {previous} to {available_agents} "
            f"({drop_ratio:.0%}); pacing falls back to progressive this tick",
            campaign_id=campaign_id,
        )
        return drop
