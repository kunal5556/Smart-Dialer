import asyncio

import pytest

from app.providers.base import OriginateRequest, ProviderEvent
from app.providers.errors import ProviderTimeout
from app.providers.mock_a import MockProviderA, default_behaviour
from app.state_machines.call_sm import rank
from app.models.enums import CallState

CALL_COUNT = 200


def make_request(index: int) -> OriginateRequest:
    return OriginateRequest(
        call_id=f"call-{index}",
        campaign_id="campaign-1",
        phone_number=f"+1555{index:07d}",
        timeout_seconds=1.0,
    )


@pytest.fixture
async def provider():
    collected: list[ProviderEvent] = []

    async def on_event(event: ProviderEvent) -> None:
        collected.append(event)

    instance = MockProviderA(on_event=on_event, seed=2024)
    instance.behaviour.setup_latency_range = (0.0, 0.0)
    instance.behaviour.ring_duration = 0.0
    instance.behaviour.avg_talk_time = 0.0
    instance.events = collected
    yield instance
    await instance.shutdown()


def test_default_behaviour_matches_the_documented_profile():
    behaviour = default_behaviour()

    assert behaviour.setup_latency_range == (0.15, 0.25)
    assert behaviour.failure_rate == pytest.approx(0.02)
    assert behaviour.hang_rate == 0.0
    assert behaviour.duplicate_rate == 0.0
    assert behaviour.out_of_order_rate == 0.0


async def test_originate_succeeds_at_the_expected_rate(provider):
    results = [await provider.originate_call(make_request(index)) for index in range(CALL_COUNT)]

    accepted = [result for result in results if result.accepted]
    assert len(accepted) >= int(CALL_COUNT * 0.93)
    assert all(result.error_code == "carrier_rejected" for result in results if not result.accepted)


async def test_provider_a_never_times_out(provider):
    for index in range(CALL_COUNT):
        await provider.originate_call(make_request(index))

    assert provider.health_snapshot().originate_timed_out == 0


async def test_events_arrive_in_rank_order(provider):
    for index in range(CALL_COUNT):
        await provider.originate_call(make_request(index))
    await asyncio.sleep(0.1)

    per_call: dict[str, list[str]] = {}
    for event in provider.events:
        per_call.setdefault(event.provider_call_id, []).append(event.event_type)

    assert per_call
    for event_types in per_call.values():
        ranks = [rank(CallState(event_type)) for event_type in event_types]
        assert ranks == sorted(ranks)
        assert len(ranks) == len(set(ranks))


async def test_no_duplicate_event_ids_across_a_large_run(provider):
    for index in range(CALL_COUNT):
        await provider.originate_call(make_request(index))
    await asyncio.sleep(0.1)

    event_ids = [event.provider_event_id for event in provider.events]
    assert len(event_ids) == len(set(event_ids))
    assert len(event_ids) >= CALL_COUNT


async def test_answered_calls_follow_the_full_script(provider):
    provider.behaviour.answer_rate = 1.0
    provider.behaviour.failure_rate = 0.0

    await provider.originate_call(make_request(1))
    await asyncio.sleep(0.05)

    assert [event.event_type for event in provider.events] == [
        "RINGING",
        "ANSWERED",
        "CONNECTED",
        "COMPLETED",
    ]


async def test_unanswered_calls_fail_with_a_reason(provider):
    provider.behaviour.answer_rate = 0.0
    provider.behaviour.failure_rate = 0.0

    await provider.originate_call(make_request(1))
    await asyncio.sleep(0.05)

    assert [event.event_type for event in provider.events] == ["RINGING", "FAILED"]
    assert provider.events[-1].payload["reason"] == "no_answer"


async def test_latency_above_the_caller_timeout_raises(provider):
    provider.behaviour.setup_latency_range = (0.05, 0.05)
    request = OriginateRequest(
        call_id="call-slow",
        campaign_id="campaign-1",
        phone_number="+15550000001",
        timeout_seconds=0.01,
    )

    with pytest.raises(ProviderTimeout):
        await provider.originate_call(request)

    assert provider.health_snapshot().originate_timed_out == 1
