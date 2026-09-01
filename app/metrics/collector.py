import asyncio
import logging

from app.config import Settings
from app.logging_config import log_event
from app.metrics.campaign_metrics import CampaignMetricsCollector
from app.repositories.campaign_repo import CampaignRepository
from app.repositories.metrics_repo import MetricsRepository

logger = logging.getLogger(__name__)


class MetricsSampler:
    def __init__(
        self,
        campaign_repository: CampaignRepository,
        metrics_collector: CampaignMetricsCollector,
        metrics_repository: MetricsRepository,
        settings: Settings,
    ) -> None:
        self._campaigns = campaign_repository
        self._collector = metrics_collector
        self._metrics = metrics_repository
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
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log_event(
                    logger,
                    logging.ERROR,
                    "metrics_sampling_failed",
                    f"Metrics sampling failed and was skipped: {error}",
                )
            await asyncio.sleep(self._settings.METRICS_SAMPLE_SECONDS)

    async def sample_once(self) -> int:
        sampled = 0
        for campaign in await self._campaigns.find_running():
            metrics = await self._collector.collect(campaign)
            await self._metrics.record_sample(metrics)
            sampled += 1
        return sampled
