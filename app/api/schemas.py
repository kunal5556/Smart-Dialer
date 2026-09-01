from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import CallState, DialingMode


class CampaignSummary(BaseModel):
    id: str
    name: str
    status: str
    dialing_mode: str
    provider_name: str
    max_concurrent_calls: int
    baseline_answer_rate: float
    created_at: datetime


class CreateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dialing_mode: DialingMode = DialingMode.PROGRESSIVE
    provider_name: str = "mock_a"
    max_concurrent_calls: int = Field(default=50, ge=0, le=100000)
    baseline_answer_rate: float = Field(default=0.3, gt=0.0, le=1.0)


class SetModeRequest(BaseModel):
    dialing_mode: DialingMode


class SeedRequest(BaseModel):
    agents: int = Field(default=10, ge=0, le=10000)
    borrowers: int = Field(default=200, ge=0, le=100000)


class AgentRecord(BaseModel):
    id: str
    name: str
    state: str
    state_version: int
    reserved_by: str | None
    lease_expires_at: datetime | None
    current_call_id: str | None
    last_heartbeat_at: datetime | None
    state_changed_at: datetime


class AgentListResponse(BaseModel):
    campaign_id: str
    state_summary: dict[str, int]
    agents: list[AgentRecord]


class CallRecord(BaseModel):
    id: str
    campaign_id: str
    agent_id: str
    borrower_id: str
    state: str
    provider_name: str
    provider_call_id: str | None
    attempt: int
    failure_reason: str | None
    created_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None


class ProviderEventRecord(BaseModel):
    provider_event_id: str
    provider_name: str
    event_type: str
    processing_status: str | None
    applied_transition: str | None
    received_at: datetime


class CallDetailResponse(BaseModel):
    call: CallRecord
    events: list[ProviderEventRecord]


class CallListResponse(BaseModel):
    campaign_id: str
    state_filter: CallState | None
    calls: list[CallRecord]


class PacingDecisionRecord(BaseModel):
    id: str
    campaign_id: str
    dialing_mode: str
    requested: int
    explanation: str
    inputs: dict[str, Any]
    created_at: datetime


class SafetyConstraintRecordSchema(BaseModel):
    name: str
    limit: int
    value: float | None
    binding: bool


class SafetyDecisionRecord(BaseModel):
    id: str
    campaign_id: str
    pacing_decision_id: str | None
    requested: int
    approved: int
    verdict: str
    binding_constraint: str | None
    snapshot_age_ms: int | None
    constraints: list[SafetyConstraintRecordSchema]
    created_at: datetime


class MetricsResponse(BaseModel):
    campaign_id: str
    collected_at: datetime
    calls_initiated: int
    calls_connected: int
    calls_completed: int
    calls_failed: int
    calls_cancelled: int
    calls_ringing: int
    active_calls: int
    peak_concurrent_calls: int
    answer_rate: float | None
    average_talk_time_seconds: float
    average_setup_time_ms: float
    agent_states: dict[str, int]
    talk_utilization: float | None
    productive_utilization: float | None
    safety_verdicts: dict[str, int]
    progressive_fallbacks: int
    reservation_contention: int
    retry_attempts: int
    provider_failures: int


class ProviderHealthRecord(BaseModel):
    provider_name: str
    status: str
    request_count: int
    success_rate: float
    failure_rate: float
    timeout_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    consecutive_failures: int
    events_received: int
    low_confidence: bool
    computed_at: datetime


class OutageRequest(BaseModel):
    seconds: float = Field(default=30.0, ge=0.0, le=600.0)


class SimulationRequest(BaseModel):
    scenario: str = "A"
    dialing_mode: DialingMode = DialingMode.PROGRESSIVE
    agents: int = Field(default=10, ge=1, le=500)
    borrowers: int = Field(default=200, ge=1, le=20000)
    duration_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    time_scale: float = Field(default=60.0, ge=1.0, le=500.0)
    seed: int = 1234
    workers: int = Field(default=1, ge=1, le=8)


class SimulationStatus(BaseModel):
    id: str
    scenario: str
    dialing_mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    passed: bool | None
    violations: list[str]
    error: str | None
    metrics: MetricsResponse | None


class FaultRequest(BaseModel):
    fault: str
    provider_name: str = "mock_b"
    seconds: float = Field(default=30.0, ge=0.0, le=600.0)
    agents_offline: int = Field(default=5, ge=0, le=10000)
    campaign_id: str | None = None


class FaultResponse(BaseModel):
    fault: str
    detail: str
    affected: int
