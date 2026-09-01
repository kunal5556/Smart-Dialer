from datetime import datetime

from pydantic import Field

from app.models.base import MongoModel, utc_now
from app.models.enums import AgentState


class Agent(MongoModel):
    campaign_id: str
    name: str
    state: AgentState = AgentState.OFFLINE
    state_version: int = Field(default=0, ge=0)
    reserved_by: str | None = None
    reserved_at: datetime | None = None
    lease_expires_at: datetime | None = None
    current_call_id: str | None = None
    last_heartbeat_at: datetime | None = None
    state_changed_at: datetime = Field(default_factory=utc_now)
    busy_time_ms: int = Field(default=0, ge=0)
    available_time_ms: int = Field(default=0, ge=0)
    connected_time_ms: int = Field(default=0, ge=0)
    wrap_up_time_ms: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
