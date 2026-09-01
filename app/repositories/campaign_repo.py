from pymongo import ReturnDocument

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, DialingMode
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

    async def find_all(self, limit: int = 200) -> list[Campaign]:
        cursor = self.collection.find({}).sort("created_at", -1).limit(limit)
        return [Campaign.from_mongo(document) async for document in cursor]

    async def insert(self, campaign: Campaign) -> Campaign:
        await self.collection.insert_one(campaign.to_mongo())
        return campaign

    async def set_status(self, campaign_id: str, status: CampaignStatus) -> Campaign | None:
        return await self._update(campaign_id, {"status": status.value})

    async def set_dialing_mode(self, campaign_id: str, mode: DialingMode) -> Campaign | None:
        return await self._update(campaign_id, {"dialing_mode": mode.value})

    async def _update(self, campaign_id: str, updates: dict) -> Campaign | None:
        document = await self.collection.find_one_and_update(
            {"_id": campaign_id},
            {"$set": {**updates, "updated_at": self.now()}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return Campaign.from_mongo(document)
