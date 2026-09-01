from enum import Enum

from pymongo.errors import DuplicateKeyError

from app.models.enums import EventProcessingStatus
from app.models.provider_event import ProviderEvent as ProviderEventDocument
from app.providers.base import ProviderEvent
from app.repositories.base import COLLECTION_PROVIDER_EVENTS, BaseRepository


class EventRecordResult(str, Enum):
    RECORDED = "RECORDED"
    DUPLICATE = "DUPLICATE"


class EventRepository(BaseRepository):
    collection_name = COLLECTION_PROVIDER_EVENTS

    async def record_event(self, event: ProviderEvent) -> EventRecordResult:
        document = ProviderEventDocument(
            provider_name=event.provider_name,
            provider_event_id=event.provider_event_id,
            provider_call_id=event.provider_call_id,
            event_type=event.event_type,
            provider_timestamp=event.provider_timestamp,
        )
        try:
            await self.collection.insert_one(document.to_mongo())
        except DuplicateKeyError:
            return EventRecordResult.DUPLICATE
        return EventRecordResult.RECORDED

    async def mark_processed(
        self,
        provider_name: str,
        provider_event_id: str,
        status: EventProcessingStatus,
        applied_transition: str | None = None,
    ) -> None:
        await self.collection.update_one(
            {"provider_name": provider_name, "provider_event_id": provider_event_id},
            {
                "$set": {
                    "processing_status": status.value,
                    "applied_transition": applied_transition,
                }
            },
        )

    async def find_by_provider_event_id(
        self,
        provider_name: str,
        provider_event_id: str,
    ) -> ProviderEventDocument | None:
        document = await self.collection.find_one(
            {"provider_name": provider_name, "provider_event_id": provider_event_id}
        )
        if document is None:
            return None
        return ProviderEventDocument.from_mongo(document)

    async def find_for_call(self, provider_call_id: str) -> list[ProviderEventDocument]:
        cursor = self.collection.find({"provider_call_id": provider_call_id}).sort("received_at", 1)
        return [ProviderEventDocument.from_mongo(document) async for document in cursor]
