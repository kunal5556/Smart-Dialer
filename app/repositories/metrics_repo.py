from datetime import datetime

from app.metrics.campaign_metrics import CampaignMetrics
from app.repositories.base import COLLECTION_METRICS_SAMPLES, BaseRepository


class MetricsRepository(BaseRepository):
    collection_name = COLLECTION_METRICS_SAMPLES

    async def record_sample(self, metrics: CampaignMetrics) -> None:
        await self.collection.insert_one(metrics.to_document())

    async def find_history(
        self,
        campaign_id: str,
        since: datetime,
        limit: int,
    ) -> list[dict]:
        cursor = (
            self.collection.find(
                {"campaign_id": campaign_id, "collected_at": {"$gte": since}},
                {"_id": 0},
            )
            .sort("collected_at", 1)
            .limit(limit)
        )
        return [document async for document in cursor]
