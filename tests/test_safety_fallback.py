import pytest

from app.models.enums import ProviderHealthStatus
from app.safety.fallback import (
    FALLBACK_AVAILABILITY_DROP,
    FALLBACK_EXCESSIVE_RINGING,
    FALLBACK_HIGH_FAILURE_RATE,
    FALLBACK_PROVIDER_DEGRADED,
    FALLBACK_STALE_STATE,
    FallbackInputs,
    should_fallback_to_progressive,
)


def healthy_inputs(**overrides) -> FallbackInputs:
    values = {
        "provider_status": ProviderHealthStatus.HEALTHY,
        "availability_drop_ratio": 0.0,
        "availability_drop_threshold": 0.25,
        "ringing_calls": 2,
        "ringing_ceiling": 10,
        "call_failure_rate": 0.05,
        "failure_rate_threshold": 0.3,
        "snapshot_is_stale": False,
    }
    values.update(overrides)
    return FallbackInputs(**values)


def test_healthy_system_does_not_fall_back():
    assert should_fallback_to_progressive(healthy_inputs()) is None


def test_stale_state_wins_over_everything():
    reason = should_fallback_to_progressive(
        healthy_inputs(
            snapshot_is_stale=True,
            provider_status=ProviderHealthStatus.UNHEALTHY,
            availability_drop_ratio=0.9,
        )
    )

    assert reason == FALLBACK_STALE_STATE


@pytest.mark.parametrize(
    "status",
    [ProviderHealthStatus.DEGRADED, ProviderHealthStatus.UNHEALTHY],
)
def test_provider_degradation_triggers_fallback(status):
    assert (
        should_fallback_to_progressive(healthy_inputs(provider_status=status))
        == FALLBACK_PROVIDER_DEGRADED
    )


def test_availability_drop_triggers_fallback():
    assert (
        should_fallback_to_progressive(healthy_inputs(availability_drop_ratio=0.4))
        == FALLBACK_AVAILABILITY_DROP
    )


def test_availability_drop_below_threshold_does_not_trigger():
    assert should_fallback_to_progressive(healthy_inputs(availability_drop_ratio=0.1)) is None


def test_excessive_ringing_triggers_fallback():
    assert (
        should_fallback_to_progressive(healthy_inputs(ringing_calls=11, ringing_ceiling=10))
        == FALLBACK_EXCESSIVE_RINGING
    )


def test_high_failure_rate_triggers_fallback():
    assert (
        should_fallback_to_progressive(healthy_inputs(call_failure_rate=0.5))
        == FALLBACK_HIGH_FAILURE_RATE
    )


def test_failure_rate_at_the_threshold_does_not_trigger():
    assert should_fallback_to_progressive(healthy_inputs(call_failure_rate=0.3)) is None
