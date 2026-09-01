from datetime import datetime

from pydantic import Field

from app.models.base import MongoModel, utc_now
from app.models.enums import EventProcessingStatus


class ProviderEvent(MongoModel):
    provider_name: str
    provider_event_id: str
    provider_call_id: str
    event_type: str
    provider_timestamp: datetime | None = None
    received_at: datetime = Field(default_factory=utc_now)
    processing_status: EventProcessingStatus | None = None
    applied_transition: str | None = None
