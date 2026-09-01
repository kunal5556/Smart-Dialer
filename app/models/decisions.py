from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import MongoModel, utc_now
from app.models.enums import DialingMode, SafetyVerdict


class PacingDecision(MongoModel):
    campaign_id: str
    dialing_mode: DialingMode
    requested: int = Field(ge=0)
    inputs: dict[str, Any] = Field(default_factory=dict)
    explanation: str
    created_at: datetime = Field(default_factory=utc_now)


class SafetyConstraintRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    limit: int
    value: float | None = None
    binding: bool = False


class SafetyDecision(MongoModel):
    campaign_id: str
    pacing_decision_id: str | None = None
    requested: int = Field(ge=0)
    approved: int = Field(ge=0)
    verdict: SafetyVerdict
    constraints: list[SafetyConstraintRecord] = Field(default_factory=list)
    binding_constraint: str | None = None
    snapshot_age_ms: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
