import pytest

from app.pacing.answer_rate import (
    BASELINE_WEIGHT,
    RECENT_WEIGHT,
    blended_answer_rate,
    clamp,
    is_volatile,
    observed_answer_rate,
)

MIN_RATE = 0.05
MAX_RATE = 0.95


def test_weights_sum_to_one():
    assert RECENT_WEIGHT + BASELINE_WEIGHT == pytest.approx(1.0)


def test_observed_answer_rate_needs_data():
    assert observed_answer_rate(0, 0) is None
    assert observed_answer_rate(5, 0) is None


def test_observed_answer_rate_is_a_simple_ratio():
    assert observed_answer_rate(3, 12) == 0.25
    assert observed_answer_rate(0, 10) == 0.0


def test_blend_uses_the_baseline_when_there_is_no_history():
    assert blended_answer_rate(None, 0.4, MIN_RATE, MAX_RATE) == pytest.approx(0.4)


def test_blend_favours_recent_experience():
    blended = blended_answer_rate(0.6, 0.2, MIN_RATE, MAX_RATE)

    assert blended == pytest.approx(0.7 * 0.6 + 0.3 * 0.2)
    assert blended > 0.4


def test_blend_is_clamped_at_the_lower_bound():
    assert blended_answer_rate(0.0, 0.01, MIN_RATE, MAX_RATE) == MIN_RATE


def test_blend_is_clamped_at_the_upper_bound():
    assert blended_answer_rate(1.0, 1.0, MIN_RATE, MAX_RATE) == MAX_RATE


def test_the_baseline_cushions_a_run_of_unlucky_calls():
    baseline = 0.3
    unlucky = blended_answer_rate(0.0, baseline, MIN_RATE, MAX_RATE)

    assert unlucky > 0.0
    assert unlucky == pytest.approx(BASELINE_WEIGHT * baseline)
    assert unlucky < baseline


def test_volatility_needs_two_windows_of_data():
    assert is_volatile(None, 0.5, 0.15) is False
    assert is_volatile(0.5, None, 0.15) is False


def test_a_large_move_is_volatile():
    assert is_volatile(0.1, 0.7, 0.15) is True
    assert is_volatile(0.7, 0.1, 0.15) is True


def test_a_small_move_is_not_volatile():
    assert is_volatile(0.30, 0.35, 0.15) is False


def test_a_move_exactly_at_the_threshold_is_not_volatile():
    assert is_volatile(0.25, 0.5, 0.25) is False


def test_clamp_bounds_values():
    assert clamp(5.0, 0.0, 1.0) == 1.0
    assert clamp(-5.0, 0.0, 1.0) == 0.0
    assert clamp(0.5, 0.0, 1.0) == 0.5
