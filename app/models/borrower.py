from datetime import datetime

from pydantic import Field

from app.models.base import MongoModel, utc_now
from app.models.enums import BorrowerStatus


class Borrower(MongoModel):
    campaign_id: str
    name: str
    phone_number: str
    status: BorrowerStatus = BorrowerStatus.PENDING
    state_version: int = Field(default=0, ge=0)
    reserved_by: str | None = None
    reserved_at: datetime | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    last_attempt_at: datetime | None = None
    next_eligible_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
