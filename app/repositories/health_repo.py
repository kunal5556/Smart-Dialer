from dataclasses import asdict
from datetime import datetime

from app.repositories.base import COLLECTION_PROVIDER_HEALTH_SAMPLES, BaseRepository
from app.services.provider_health import ProviderHealth


class HealthRepository(BaseRepository):
    collection_name = COLLECTION_PROVIDER_HEALTH_SAMPLES

    async def record_snapshot(self, health: ProviderHealth) -> None:
        document = asdict(health)
        document["status"] = health.status.value
        await self.collection.insert_one(document)

    async def find_recent(
        self,
        provider_name: str,
        since: datetime,
        limit: int,
    ) -> list[dict]:
        cursor = (
            self.collection.find(
                {"provider_name": provider_name, "computed_at": {"$gte": since}},
                {"_id": 0},
            )
            .sort("computed_at", 1)
            .limit(limit)
        )
        return [document async for document in cursor]
