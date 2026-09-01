import pytest

from app.models.enums import ProviderHealthStatus
from app.services.provider_health import ProviderHealthManager, percentile

PROVIDER = "mock_a"


@pytest.fixture
def manager(test_settings) -> ProviderHealthManager:
    return ProviderHealthManager(test_settings)


def record_many(manager, count: int, success: bool, latency_ms: int = 100, timed_out=False):
    for _ in range(count):
        manager.record_originate(
            provider_name=PROVIDER,
            success=success,
            latency_ms=latency_ms,
            timed_out=timed_out,
        )


def test_no_samples_is_healthy_with_low_confidence(manager):
    health = manager.get_health(PROVIDER)

    assert health.status is ProviderHealthStatus.HEALTHY
    assert health.low_confidence is True
    assert health.request_count == 0


def test_two_failures_do_not_produce_a_hard_verdict(manager):
    record_many(manager, 2, success=False)

    health = manager.get_health(PROVIDER)

    assert health.status is ProviderHealthStatus.HEALTHY
    assert health.low_confidence is True


def test_ten_consecutive_failures_are_unhealthy(manager):
    record_many(manager, 10, success=False)

    health = manager.get_health(PROVIDER)

    assert health.status is ProviderHealthStatus.UNHEALTHY
    assert health.consecutive_failures == 10
    assert health.failure_rate == 1.0


def test_failure_rate_above_the_threshold_is_degraded(manager, test_settings):
    record_many(manager, 15, success=True)
    record_many(manager, 5, success=False)
    manager.record_originate(PROVIDER, success=True, latency_ms=100)

    health = manager.get_health(PROVIDER)

    assert health.failure_rate > test_settings.DEGRADED_FAILURE_RATE
    assert health.status is ProviderHealthStatus.DEGRADED


def test_failure_rate_below_the_threshold_stays_healthy(manager, test_settings):
    record_many(manager, 18, success=True)
    record_many(manager, 2, success=False)
    manager.record_originate(PROVIDER, success=True, latency_ms=100)

    health = manager.get_health(PROVIDER)

    assert health.failure_rate < test_settings.DEGRADED_FAILURE_RATE
    assert health.status is ProviderHealthStatus.HEALTHY


def test_one_failure_in_fifty_is_healthy(manager):
    record_many(manager, 49, success=True)
    manager.record_originate(PROVIDER, success=False, latency_ms=100)
    record_many(manager, 5, success=True)

    health = manager.get_health(PROVIDER)

    assert health.status is ProviderHealthStatus.HEALTHY
    assert health.low_confidence is False


def test_high_latency_is_degraded_even_with_full_success(manager, test_settings):
    record_many(manager, 20, success=True, latency_ms=int(test_settings.DEGRADED_LATENCY_MS) + 500)

    health = manager.get_health(PROVIDER)

    assert health.status is ProviderHealthStatus.DEGRADED
    assert health.success_rate == 1.0
    assert health.p95_latency_ms > test_settings.DEGRADED_LATENCY_MS


def test_majority_timeouts_are_unhealthy(manager):
    record_many(manager, 4, success=True)
    record_many(manager, 6, success=False, timed_out=True)

    health = manager.get_health(PROVIDER)

    assert health.status is ProviderHealthStatus.UNHEALTHY
    assert health.timeout_rate > 0.5


def test_recovery_returns_to_healthy(manager):
    record_many(manager, 10, success=False)
    assert manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY

    record_many(manager, 60, success=True)

    assert manager.get_health(PROVIDER).status is ProviderHealthStatus.HEALTHY


def test_old_samples_age_out_of_the_window(manager, test_settings, monkeypatch):
    import app.services.provider_health as provider_health

    clock = {"value": 1000.0}
    monkeypatch.setattr(provider_health.time, "monotonic", lambda: clock["value"])

    record_many(manager, 10, success=False)
    assert manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY

    clock["value"] += test_settings.HEALTH_WINDOW_SECONDS + 1

    health = manager.get_health(PROVIDER)
    assert health.request_count == 0
    assert health.status is ProviderHealthStatus.HEALTHY


def test_health_factor_matches_status(manager):
    assert manager.health_factor(PROVIDER) == 1.0

    record_many(manager, 10, success=False)
    assert manager.health_factor(PROVIDER) == 0.0

    manager_degraded = ProviderHealthManager(manager._settings)
    record_many(manager_degraded, 15, success=True)
    record_many(manager_degraded, 5, success=False)
    manager_degraded.record_originate(PROVIDER, success=True, latency_ms=1)
    assert manager_degraded.health_factor(PROVIDER) == 0.5


def test_event_counter_is_reported(manager):
    manager.record_event_received(PROVIDER)
    manager.record_event_received(PROVIDER)

    assert manager.get_health(PROVIDER).events_received == 2


def test_providers_are_tracked_independently(manager):
    record_many(manager, 10, success=False)
    manager.record_originate("mock_b", success=True, latency_ms=10)

    assert manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY
    assert manager.get_health("mock_b").status is ProviderHealthStatus.HEALTHY


def test_computation_failure_falls_back_to_degraded(manager, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("broken window")

    monkeypatch.setattr(manager, "_compute_health", explode)

    health = manager.get_health(PROVIDER)

    assert health.status is ProviderHealthStatus.DEGRADED
    assert health.low_confidence is True


def test_percentile_handles_empty_and_single_values():
    assert percentile([], 0.5) == 0.0
    assert percentile([42.0], 0.95) == 42.0
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 100.0], 0.95) == 100.0
