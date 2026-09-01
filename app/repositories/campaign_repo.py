from app.models.campaign import Campaign
from app.models.enums import CampaignStatus
from app.repositories.base import COLLECTION_CAMPAIGNS, BaseRepository


class CampaignRepository(BaseRepository):
    collection_name = COLLECTION_CAMPAIGNS

    async def find_by_id(self, campaign_id: str) -> Campaign | None:
        document = await self.collection.find_one({"_id": campaign_id})
        if document is None:
            return None
        return Campaign.from_mongo(document)

    async def find_running(self, limit: int = 100) -> list[Campaign]:
        cursor = self.collection.find({"status": CampaignStatus.RUNNING.value}).limit(limit)
        return [Campaign.from_mongo(document) async for document in cursor]
