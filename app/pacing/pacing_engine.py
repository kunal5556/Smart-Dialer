import math
from dataclasses import dataclass

from app.pacing.answer_rate import blended_answer_rate, is_volatile
from app.pacing.metrics_snapshot import PacingSnapshot
from app.safety.models import PacingRequest


@dataclass(frozen=True)
class PacingEngineConfig:
    soon_free_weight: float
    safety_margin: float
    min_answer_rate: float
    max_answer_rate: float
    volatility_threshold: float
    volatility_factor: float
    max_request_per_tick: int
    forced_answer_rate: float | None = None


def compute_request(snapshot: PacingSnapshot, config: PacingEngineConfig) -> PacingRequest:
    try:
        return _compute(snapshot, config)
    except Exception as error:
        return PacingRequest(
            requested=0,
            mode=snapshot.mode,
            snapshot_captured_at=snapshot.captured_at,
            inputs={"pacing_error": type(error).__name__},
            explanation="Pacing calculation failed, requesting no additional calls.",
        )


def _compute(snapshot: PacingSnapshot, config: PacingEngineConfig) -> PacingRequest:
    if config.forced_answer_rate is not None:
        effective_answer_rate = config.forced_answer_rate
    else:
        effective_answer_rate = blended_answer_rate(
            recent_answer_rate=snapshot.recent_answer_rate,
            baseline_answer_rate=snapshot.baseline_answer_rate,
            min_answer_rate=config.min_answer_rate,
            max_answer_rate=config.max_answer_rate,
        )
    safety_margin = config.safety_margin

    soon_free_agents = snapshot.wrap_up_agents + snapshot.long_connected_agents
    free_capacity = snapshot.available_agents + soon_free_agents * config.soon_free_weight
    calls_needed = free_capacity / effective_answer_rate
    in_flight = snapshot.ringing_calls + snapshot.initiated_calls + snapshot.reserved_agents
    raw_request = math.floor(max(0.0, calls_needed - in_flight))

    volatile = is_volatile(
        recent_answer_rate=snapshot.recent_answer_rate,
        previous_answer_rate=snapshot.previous_answer_rate,
        threshold=config.volatility_threshold,
    )
    volatility_factor = config.volatility_factor if volatile else 1.0
    health_factor = snapshot.health_factor

    scaled = raw_request * safety_margin * health_factor * volatility_factor
    requested = min(int(math.floor(scaled)), config.max_request_per_tick)

    inputs = {
        "available_agents": snapshot.available_agents,
        "reserved_agents": snapshot.reserved_agents,
        "wrap_up_agents": snapshot.wrap_up_agents,
        "long_connected_agents": snapshot.long_connected_agents,
        "soon_free_agents": soon_free_agents,
        "soon_free_weight": config.soon_free_weight,
        "free_capacity": free_capacity,
        "ringing_calls": snapshot.ringing_calls,
        "initiated_calls": snapshot.initiated_calls,
        "active_calls": snapshot.active_calls,
        "in_flight": in_flight,
        "historical_answer_rate": snapshot.recent_answer_rate,
        "baseline_answer_rate": snapshot.baseline_answer_rate,
        "effective_answer_rate": effective_answer_rate,
        "avg_talk_time_seconds": snapshot.avg_talk_time_seconds,
        "avg_setup_time_ms": snapshot.avg_setup_time_ms,
        "provider_status": snapshot.provider_status.value,
        "health_factor": health_factor,
        "volatility_factor": volatility_factor,
        "safety_margin": safety_margin,
        "calls_needed": calls_needed,
        "raw_request": raw_request,
        "requested": requested,
        "low_confidence": snapshot.low_confidence,
    }

    return PacingRequest(
        requested=requested,
        mode=snapshot.mode,
        snapshot_captured_at=snapshot.captured_at,
        inputs=inputs,
        explanation=_explain(snapshot, config, inputs),
    )


def _explain(snapshot: PacingSnapshot, config: PacingEngineConfig, inputs: dict) -> str:
    return (
        f"{snapshot.available_agents} agents free + {inputs['soon_free_agents']} soon-free "
        f"(weighted {inputs['soon_free_agents'] * config.soon_free_weight:g}) = "
        f"{inputs['free_capacity']:g} capacity; at "
        f"{inputs['effective_answer_rate'] * 100:.0f}% estimated answer rate that needs "
        f"{inputs['calls_needed']:.0f} calls; {inputs['in_flight']} already in flight leaves "
        f"{inputs['raw_request']}; x{inputs['safety_margin']:g} safety margin "
        f"x{inputs['health_factor']:g} health x{inputs['volatility_factor']:g} volatility = "
        f"{inputs['requested']} requested."
    )
