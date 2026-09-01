from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.enums import DialingMode, SafetyVerdict


@dataclass(frozen=True)
class PacingRequest:
    requested: int
    mode: DialingMode
    snapshot_captured_at: datetime
    inputs: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""


@dataclass(frozen=True)
class SafetyConstraintResult:
    name: str
    limit: int
    value: float | None = None
    binding: bool = False


@dataclass(frozen=True)
class SafetyDecision:
    campaign_id: str
    requested: int
    approved: int
    verdict: SafetyVerdict
    constraints: list[SafetyConstraintResult]
    binding_constraint: str | None
    snapshot_age_ms: int
    created_at: datetime
    fallback_reason: str | None = None
    pacing_decision_id: str | None = None
