from dataclasses import dataclass

from app.models.enums import ProviderHealthStatus

FALLBACK_PROVIDER_DEGRADED = "provider_degraded"
FALLBACK_AVAILABILITY_DROP = "availability_drop"
FALLBACK_EXCESSIVE_RINGING = "excessive_ringing"
FALLBACK_HIGH_FAILURE_RATE = "high_failure_rate"
FALLBACK_STALE_STATE = "stale_state"


@dataclass(frozen=True)
class FallbackInputs:
    provider_status: ProviderHealthStatus
    availability_drop_ratio: float
    availability_drop_threshold: float
    ringing_calls: int
    ringing_ceiling: int
    call_failure_rate: float
    failure_rate_threshold: float
    snapshot_is_stale: bool


def should_fallback_to_progressive(inputs: FallbackInputs) -> str | None:
    if inputs.snapshot_is_stale:
        return FALLBACK_STALE_STATE
    if inputs.provider_status is not ProviderHealthStatus.HEALTHY:
        return FALLBACK_PROVIDER_DEGRADED
    if inputs.availability_drop_ratio > inputs.availability_drop_threshold:
        return FALLBACK_AVAILABILITY_DROP
    if inputs.ringing_calls > inputs.ringing_ceiling:
        return FALLBACK_EXCESSIVE_RINGING
    if inputs.call_failure_rate > inputs.failure_rate_threshold:
        return FALLBACK_HIGH_FAILURE_RATE
    return None
