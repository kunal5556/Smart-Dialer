import asyncio
from datetime import datetime, timezone

import pytest

from app.models.enums import AgentState, CallState, EventProcessingStatus
from app.providers.base import ProviderEvent
from app.repositories.event_repo import EventRecordResult
from tests.conftest import prepare_dialing_call

pytestmark = pytest.mark.usefixtures("clean_call_collections")


def make_event(provider_call_id: str, event_type: str, event_id: str) -> ProviderEvent:
    return ProviderEvent(
        provider_name="mock_a",
        provider_event_id=event_id,
        provider_call_id=provider_call_id,
        event_type=event_type,
        provider_timestamp=datetime.now(timezone.utc),
    )


async def test_repeated_event_produces_one_set_of_side_effects(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    event = make_event(context.provider_call_id, "ANSWERED", "event-1")

    results = [await event_processor.process_event(event) for _ in range(5)]

    statuses = [result.status for result in results]
    assert statuses[0] is EventProcessingStatus.PROCESSED
    assert statuses[1:] == [EventProcessingStatus.DUPLICATE_IGNORED] * 4

    call = await call_repository.find_by_id(context.call.id)
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert call.state is CallState.ANSWERED
    assert agent["state"] == AgentState.CONNECTED.value
    assert agent["state_version"] == context.agent.state_version + 1
    assert await test_database["provider_events"].count_documents({}) == 1


async def test_concurrent_duplicates_produce_exactly_one_processed(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    event = make_event(context.provider_call_id, "ANSWERED", "event-concurrent")

    results = await asyncio.gather(
        *(event_processor.process_event(event) for _ in range(10))
    )

    statuses = [result.status for result in results]
    assert statuses.count(EventProcessingStatus.PROCESSED) == 1
    assert statuses.count(EventProcessingStatus.DUPLICATE_IGNORED) == 9

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state_version"] == context.agent.state_version + 1


async def test_record_event_reports_duplicates(event_repository):
    event = make_event("provider-call-x", "RINGING", "event-record-1")

    first = await event_repository.record_event(event)
    second = await event_repository.record_event(event)

    assert first is EventRecordResult.RECORDED
    assert second is EventRecordResult.DUPLICATE


async def test_same_event_id_from_a_different_provider_is_not_a_duplicate(event_repository):
    first = make_event("provider-call-y", "RINGING", "shared-id")
    second = ProviderEvent(
        provider_name="mock_b",
        provider_event_id="shared-id",
        provider_call_id="provider-call-y",
        event_type="RINGING",
        provider_timestamp=datetime.now(timezone.utc),
    )

    assert await event_repository.record_event(first) is EventRecordResult.RECORDED
    assert await event_repository.record_event(second) is EventRecordResult.RECORDED


async def test_every_event_receives_a_processing_status(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    await event_processor.process_event(
        make_event(context.provider_call_id, "RINGING", "event-a")
    )
    await event_processor.process_event(make_event("unknown-call", "RINGING", "event-b"))
    await event_processor.process_event(
        make_event(context.provider_call_id, "NOT_A_REAL_EVENT", "event-c")
    )

    documents = await test_database["provider_events"].find({}).to_list(None)
    assert len(documents) == 3
    assert all(document["processing_status"] is not None for document in documents)


async def test_unknown_provider_call_id_is_invalid(test_database, event_processor):
    result = await event_processor.process_event(
        make_event("never-existed", "ANSWERED", "event-unknown")
    )

    assert result.status is EventProcessingStatus.INVALID_IGNORED
    stored = await test_database["provider_events"].find_one(
        {"provider_event_id": "event-unknown"}
    )
    assert stored["processing_status"] == EventProcessingStatus.INVALID_IGNORED.value


async def test_unknown_event_type_is_invalid(test_database, event_processor, call_repository):
    context = await prepare_dialing_call(test_database, call_repository)

    result = await event_processor.process_event(
        make_event(context.provider_call_id, "PIZZA_DELIVERED", "event-weird")
    )

    assert result.status is EventProcessingStatus.INVALID_IGNORED
    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.INITIATED


async def test_processed_event_records_the_applied_transition(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    result = await event_processor.process_event(
        make_event(context.provider_call_id, "RINGING", "event-transition")
    )

    assert result.applied_transition == "INITIATED->RINGING"
    stored = await test_database["provider_events"].find_one(
        {"provider_event_id": "event-transition"}
    )
    assert stored["applied_transition"] == "INITIATED->RINGING"
