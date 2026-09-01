import pytest

from app.models.base import utc_now
from app.models.enums import DialingMode, ProviderHealthStatus
from app.pacing.metrics_snapshot import PacingSnapshot
from app.pacing.pacing_engine import PacingEngineConfig, compute_request

BASE_CONFIG = PacingEngineConfig(
    soon_free_weight=0.5,
    safety_margin=0.85,
    min_answer_rate=0.05,
    max_answer_rate=0.95,
    volatility_threshold=0.15,
    volatility_factor=0.6,
    max_request_per_tick=50,
)


def make_snapshot(**overrides) -> PacingSnapshot:
    values = {
        "campaign_id": "campaign-1",
        "mode": DialingMode.PREDICTIVE,
        "available_agents": 12,
        "reserved_agents": 0,
        "dialing_agents": 0,
        "connected_agents": 0,
        "wrap_up_agents": 3,
        "long_connected_agents": 0,
        "previous_available_agents": 12,
        "ringing_calls": 21,
        "initiated_calls": 0,
        "active_calls": 21,
        "recent_answer_rate": 0.32,
        "previous_answer_rate": 0.32,
        "baseline_answer_rate": 0.32,
        "avg_talk_time_seconds": 120.0,
        "avg_setup_time_ms": 200.0,
        "provider_status": ProviderHealthStatus.HEALTHY,
        "health_factor": 1.0,
        "captured_at": utc_now(),
    }
    values.update(overrides)
    return PacingSnapshot(**values)


def test_worked_example_from_the_roadmap_requests_seventeen():
    request = compute_request(make_snapshot(), BASE_CONFIG)

    assert request.inputs["free_capacity"] == 13.5
    assert request.inputs["effective_answer_rate"] == pytest.approx(0.32)
    assert request.inputs["calls_needed"] == pytest.approx(42.1875)
    assert request.inputs["in_flight"] == 21
    assert request.inputs["raw_request"] == 21
    assert request.requested == 17


def test_same_snapshot_produces_the_same_request():
    snapshot = make_snapshot()

    first = compute_request(snapshot, BASE_CONFIG)
    second = compute_request(snapshot, BASE_CONFIG)

    assert first.requested == second.requested
    assert first.inputs == second.inputs
    assert first.explanation == second.explanation


def test_halving_the_answer_rate_roughly_doubles_calls_needed():
    high = compute_request(
        make_snapshot(recent_answer_rate=0.4, previous_answer_rate=0.4, baseline_answer_rate=0.4),
        BASE_CONFIG,
    )
    low = compute_request(
        make_snapshot(recent_answer_rate=0.2, previous_answer_rate=0.2, baseline_answer_rate=0.2),
        BASE_CONFIG,
    )

    assert low.inputs["calls_needed"] == pytest.approx(high.inputs["calls_needed"] * 2)


def test_zero_observed_answer_rate_is_clamped():
    request = compute_request(
        make_snapshot(
            recent_answer_rate=0.0,
            previous_answer_rate=0.0,
            baseline_answer_rate=0.05,
            ringing_calls=0,
            active_calls=0,
        ),
        BASE_CONFIG,
    )

    assert request.inputs["effective_answer_rate"] == BASE_CONFIG.min_answer_rate
    assert request.requested == BASE_CONFIG.max_request_per_tick


def test_full_answer_rate_is_clamped_to_the_maximum():
    request = compute_request(
        make_snapshot(
            recent_answer_rate=1.0, previous_answer_rate=1.0, baseline_answer_rate=1.0
        ),
        BASE_CONFIG,
    )

    assert request.inputs["effective_answer_rate"] == BASE_CONFIG.max_answer_rate


def test_blend_weights_recent_experience_over_the_baseline():
    request = compute_request(
        make_snapshot(recent_answer_rate=0.5, previous_answer_rate=0.5, baseline_answer_rate=0.2),
        BASE_CONFIG,
    )

    assert request.inputs["effective_answer_rate"] == pytest.approx(0.7 * 0.5 + 0.3 * 0.2)


def test_degraded_health_halves_the_request():
    healthy = compute_request(make_snapshot(), BASE_CONFIG)
    degraded = compute_request(
        make_snapshot(health_factor=0.5, provider_status=ProviderHealthStatus.DEGRADED),
        BASE_CONFIG,
    )

    assert degraded.requested == int(healthy.inputs["raw_request"] * 0.85 * 0.5)
    assert degraded.requested < healthy.requested


def test_unhealthy_health_zeroes_the_request():
    request = compute_request(
        make_snapshot(health_factor=0.0, provider_status=ProviderHealthStatus.UNHEALTHY),
        BASE_CONFIG,
    )

    assert request.requested == 0


def test_volatility_reduces_the_request():
    stable = compute_request(make_snapshot(), BASE_CONFIG)
    volatile = compute_request(
        make_snapshot(recent_answer_rate=0.32, previous_answer_rate=0.7), BASE_CONFIG
    )

    assert volatile.inputs["volatility_factor"] == BASE_CONFIG.volatility_factor
    assert stable.inputs["volatility_factor"] == 1.0
    assert volatile.requested < stable.requested


def test_progressive_equivalence_when_the_answer_rate_is_forced_to_one():
    config = PacingEngineConfig(
        soon_free_weight=0.0,
        safety_margin=1.0,
        min_answer_rate=0.05,
        max_answer_rate=0.95,
        volatility_threshold=0.15,
        volatility_factor=0.6,
        max_request_per_tick=50,
        forced_answer_rate=1.0,
    )
    snapshot = make_snapshot(mode=DialingMode.PROGRESSIVE, ringing_calls=4, active_calls=4)

    request = compute_request(snapshot, config)

    assert request.inputs["effective_answer_rate"] == 1.0
    assert request.inputs["free_capacity"] == snapshot.available_agents
    assert request.requested == int(
        request.inputs["free_capacity"] - request.inputs["in_flight"]
    )


def test_request_is_never_negative():
    request = compute_request(
        make_snapshot(available_agents=0, wrap_up_agents=0, ringing_calls=50, active_calls=50),
        BASE_CONFIG,
    )

    assert request.requested == 0


def test_request_is_capped_per_tick():
    request = compute_request(
        make_snapshot(available_agents=5000, ringing_calls=0, active_calls=0), BASE_CONFIG
    )

    assert request.requested == BASE_CONFIG.max_request_per_tick


def test_reserved_agents_count_as_in_flight():
    without = compute_request(make_snapshot(reserved_agents=0), BASE_CONFIG)
    with_reserved = compute_request(make_snapshot(reserved_agents=5), BASE_CONFIG)

    assert with_reserved.inputs["in_flight"] == without.inputs["in_flight"] + 5
    assert with_reserved.requested < without.requested


def test_missing_history_falls_back_to_the_baseline_and_flags_low_confidence():
    request = compute_request(
        make_snapshot(recent_answer_rate=None, previous_answer_rate=None), BASE_CONFIG
    )

    assert request.inputs["effective_answer_rate"] == pytest.approx(0.32)
    assert request.inputs["low_confidence"] is True


def test_a_broken_snapshot_degrades_to_zero_rather_than_a_large_number():
    class BrokenSnapshot:
        mode = DialingMode.PREDICTIVE
        captured_at = utc_now()

        def __getattr__(self, name):
            raise RuntimeError("snapshot is broken")

    request = compute_request(BrokenSnapshot(), BASE_CONFIG)

    assert request.requested == 0
    assert request.inputs["pacing_error"] == "RuntimeError"


def test_request_carries_the_snapshot_timestamp_for_staleness_checks():
    snapshot = make_snapshot()

    request = compute_request(snapshot, BASE_CONFIG)

    assert request.snapshot_captured_at == snapshot.captured_at
    assert request.mode is snapshot.mode
