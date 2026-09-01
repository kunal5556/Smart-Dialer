import asyncio

import pytest

from app.providers.base import (
    OriginateRequest,
    ProviderCallStatus,
    ProviderEvent,
    TelecomProvider,
)
from app.providers.errors import ProviderRejected, ProviderUnavailable
from app.providers.mock_a import MockProviderA
from app.providers.mock_b import MockProviderB
from app.providers.registry import ProviderRegistry, build_registry

PROVIDER_CLASSES = [MockProviderA, MockProviderB]


def make_request(call_id: str = "call-1", phone_number: str = "+15550000001") -> OriginateRequest:
    return OriginateRequest(
        call_id=call_id,
        campaign_id="campaign-1",
        phone_number=phone_number,
        timeout_seconds=1.0,
    )


def make_reliable_provider(provider_class, collected: list[ProviderEvent]):
    async def on_event(event: ProviderEvent) -> None:
        collected.append(event)

    provider = provider_class(on_event=on_event, seed=1234)
    provider.behaviour.setup_latency_range = (0.0, 0.0)
    provider.behaviour.failure_rate = 0.0
    provider.behaviour.hang_rate = 0.0
    provider.behaviour.duplicate_rate = 0.0
    provider.behaviour.out_of_order_rate = 0.0
    provider.behaviour.ring_duration = 0.0
    provider.behaviour.avg_talk_time = 0.0
    return provider


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_mock_providers_satisfy_the_protocol(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)

    assert isinstance(provider, TelecomProvider)
    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_originate_returns_a_provider_call_id(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)

    result = await provider.originate_call(make_request())

    assert result.accepted is True
    assert result.provider_call_id is not None
    assert result.error_code is None
    assert result.latency_ms >= 0
    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_events_are_delivered_through_the_callback(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)
    provider.behaviour.answer_rate = 1.0

    result = await provider.originate_call(make_request())
    await asyncio.sleep(0.05)

    assert [event.event_type for event in collected] == [
        "RINGING",
        "ANSWERED",
        "CONNECTED",
        "COMPLETED",
    ]
    assert all(event.provider_call_id == result.provider_call_id for event in collected)
    assert all(event.provider_name == provider.name for event in collected)
    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_call_status_lifecycle(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)
    provider.behaviour.answer_rate = 1.0

    assert await provider.get_call_status("never-existed") is ProviderCallStatus.UNKNOWN

    result = await provider.originate_call(make_request())
    await asyncio.sleep(0.05)

    assert await provider.get_call_status(result.provider_call_id) is ProviderCallStatus.COMPLETED
    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_hangup_stops_further_events(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)
    provider.behaviour.answer_rate = 1.0
    provider.behaviour.ring_duration = 0.05
    provider.behaviour.avg_talk_time = 0.05

    result = await provider.originate_call(make_request())
    await provider.hangup_call(result.provider_call_id)
    await asyncio.sleep(0.2)

    assert collected == []
    assert await provider.get_call_status(result.provider_call_id) is ProviderCallStatus.COMPLETED
    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_hangup_of_an_unknown_call_is_a_no_op(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)

    await provider.hangup_call("never-existed")

    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_blank_phone_number_is_rejected(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)

    with pytest.raises(ProviderRejected):
        await provider.originate_call(make_request(phone_number="   "))

    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_health_snapshot_counts_originate_outcomes(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)

    await provider.originate_call(make_request())
    snapshot = provider.health_snapshot()

    assert snapshot.provider_name == provider.name
    assert snapshot.in_outage is False
    assert snapshot.originate_attempts == 1
    assert snapshot.originate_accepted == 1
    assert snapshot.originate_rejected == 0
    assert snapshot.originate_timed_out == 0
    await provider.shutdown()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_shutdown_cancels_pending_event_tasks(provider_class):
    collected: list[ProviderEvent] = []
    provider = make_reliable_provider(provider_class, collected)
    provider.behaviour.answer_rate = 1.0
    provider.behaviour.ring_duration = 5.0

    await provider.originate_call(make_request())
    await provider.shutdown()
    await asyncio.sleep(0.05)

    assert collected == []
    assert provider._pending_tasks == set()


@pytest.mark.parametrize("provider_class", PROVIDER_CLASSES)
async def test_same_seed_produces_identical_event_streams(provider_class):
    streams = []
    for _ in range(2):
        collected: list[ProviderEvent] = []

        async def on_event(event: ProviderEvent, sink=collected) -> None:
            sink.append(event)

        provider = provider_class(on_event=on_event, seed=99)
        provider.behaviour.setup_latency_range = (0.0, 0.0)
        provider.behaviour.hang_rate = 0.0
        provider.behaviour.ring_duration = 0.0
        provider.behaviour.avg_talk_time = 0.0
        for index in range(15):
            try:
                await provider.originate_call(make_request(call_id=f"call-{index}"))
            except Exception:
                pass
        await asyncio.sleep(0.1)
        streams.append([(event.provider_event_id, event.event_type) for event in collected])
        await provider.shutdown()

    assert streams[0] == streams[1]
    assert streams[0]


async def test_registry_resolves_registered_providers():
    async def on_event(event: ProviderEvent) -> None:
        return None

    registry = build_registry(on_event=on_event, seed=7)

    assert registry.names() == ["mock_a", "mock_b"]
    assert registry.get("mock_a").name == "mock_a"
    assert registry.get("mock_b").name == "mock_b"
    await registry.shutdown()


async def test_registry_rejects_an_unknown_provider():
    registry = ProviderRegistry()

    with pytest.raises(ProviderUnavailable):
        registry.get("does_not_exist")
