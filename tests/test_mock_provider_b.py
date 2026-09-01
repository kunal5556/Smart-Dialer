import asyncio

import pytest

from app.models.enums import CallState
from app.providers.base import OriginateRequest, ProviderEvent
from app.providers.errors import ProviderTimeout
from app.providers.mock_b import MockProviderB, default_behaviour
from app.state_machines.call_sm import rank

CALL_COUNT = 200


def make_request(index: int, timeout_seconds: float = 1.0) -> OriginateRequest:
    return OriginateRequest(
        call_id=f"call-{index}",
        campaign_id="campaign-1",
        phone_number=f"+1555{index:07d}",
        timeout_seconds=timeout_seconds,
    )


@pytest.fixture
async def provider():
    collected: list[ProviderEvent] = []

    async def on_event(event: ProviderEvent) -> None:
        collected.append(event)

    instance = MockProviderB(on_event=on_event, seed=2024)
    instance.behaviour.setup_latency_range = (0.0, 0.0)
    instance.behaviour.ring_duration = 0.0
    instance.behaviour.avg_talk_time = 0.0
    instance.events = collected
    yield instance
    await instance.shutdown()


def test_default_behaviour_matches_the_documented_profile():
    behaviour = default_behaviour()

    assert behaviour.setup_latency_range == (0.8, 2.5)
    assert behaviour.failure_rate == pytest.approx(0.15)
    assert behaviour.hang_rate == pytest.approx(0.08)
    assert behaviour.duplicate_rate == pytest.approx(0.10)
    assert behaviour.out_of_order_rate == pytest.approx(0.10)


async def run_calls(provider, count: int = CALL_COUNT, timeout_seconds: float = 0.01) -> int:
    timeouts = 0
    for index in range(count):
        try:
            await provider.originate_call(make_request(index, timeout_seconds))
        except ProviderTimeout:
            timeouts += 1
    await asyncio.sleep(0.1)
    return timeouts


async def test_provider_b_times_out_sometimes(provider):
    timeouts = await run_calls(provider)

    assert timeouts > 0
    assert provider.health_snapshot().originate_timed_out == timeouts


async def test_provider_b_rejects_originates_more_often_than_provider_a(provider):
    await run_calls(provider)
    snapshot = provider.health_snapshot()

    assert snapshot.originate_rejected > 0
    assert snapshot.originate_attempts == CALL_COUNT


async def test_provider_b_emits_duplicate_event_ids(provider):
    await run_calls(provider)

    event_ids = [event.provider_event_id for event in provider.events]
    assert len(event_ids) > len(set(event_ids))


async def test_provider_b_emits_out_of_order_sequences(provider):
    await run_calls(provider)

    per_call: dict[str, list[str]] = {}
    for event in provider.events:
        per_call.setdefault(event.provider_call_id, []).append(event.event_type)

    out_of_order = 0
    for event_types in per_call.values():
        ranks = [rank(CallState(event_type)) for event_type in event_types]
        if ranks != sorted(ranks):
            out_of_order += 1

    assert out_of_order > 0


async def test_force_outage_makes_every_originate_time_out(provider):
    provider.force_outage(60)

    for index in range(5):
        with pytest.raises(ProviderTimeout):
            await provider.originate_call(make_request(index, timeout_seconds=0.01))

    assert provider.health_snapshot().in_outage is True
    assert provider.health_snapshot().originate_timed_out == 5


async def test_clear_outage_restores_normal_behaviour(provider):
    provider.behaviour.hang_rate = 0.0
    provider.behaviour.failure_rate = 0.0
    provider.force_outage(60)

    with pytest.raises(ProviderTimeout):
        await provider.originate_call(make_request(1, timeout_seconds=0.01))

    provider.clear_outage()
    result = await provider.originate_call(make_request(2, timeout_seconds=0.5))

    assert provider.health_snapshot().in_outage is False
    assert result.accepted is True


async def test_outage_expires_on_its_own(provider):
    provider.behaviour.hang_rate = 0.0
    provider.behaviour.failure_rate = 0.0
    provider.force_outage(0.05)

    with pytest.raises(ProviderTimeout):
        await provider.originate_call(make_request(1, timeout_seconds=0.01))

    await asyncio.sleep(0.1)
    result = await provider.originate_call(make_request(2, timeout_seconds=0.5))

    assert result.accepted is True
