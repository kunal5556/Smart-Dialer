from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import MongoModel, utc_now
from app.models.enums import CampaignStatus, DialingMode


class PacingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safety_margin: float = Field(default=0.85, ge=0.0, le=1.0)
    min_answer_rate: float = Field(default=0.05, gt=0.0, le=1.0)
    max_answer_rate: float = Field(default=0.95, gt=0.0, le=1.0)
    max_ringing_ratio: float = Field(default=2.0, ge=0.0)
    baseline_answer_rate: float = Field(default=0.3, gt=0.0, le=1.0)


class CampaignStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls_initiated: int = Field(default=0, ge=0)
    calls_answered: int = Field(default=0, ge=0)
    calls_failed: int = Field(default=0, ge=0)
    answer_rate_window: list[bool] = Field(default_factory=list)


class Campaign(MongoModel):
    name: str
    status: CampaignStatus = CampaignStatus.DRAFT
    dialing_mode: DialingMode = DialingMode.PROGRESSIVE
    max_concurrent_calls: int = Field(default=50, ge=0)
    provider_name: str = "mock_a"
    pacing_config: PacingConfig = Field(default_factory=PacingConfig)
    stats: CampaignStats = Field(default_factory=CampaignStats)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
