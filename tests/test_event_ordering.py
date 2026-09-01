from datetime import datetime, timezone

import pytest

from app.models.enums import AgentState, BorrowerStatus, CallState, EventProcessingStatus
from app.providers.base import ProviderEvent
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


async def deliver(event_processor, provider_call_id, event_types):
    results = []
    for index, event_type in enumerate(event_types):
        results.append(
            await event_processor.process_event(
                make_event(provider_call_id, event_type, f"event-{index}-{event_type}")
            )
        )
    return results


async def test_completed_then_answered_then_ringing_leaves_the_call_completed(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    results = await deliver(
        event_processor, context.provider_call_id, ["COMPLETED", "ANSWERED", "RINGING"]
    )

    assert [result.status for result in results] == [
        EventProcessingStatus.PROCESSED,
        EventProcessingStatus.STALE_IGNORED,
        EventProcessingStatus.STALE_IGNORED,
    ]

    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.COMPLETED
    assert call.terminal is True

    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})
    assert borrower["attempt_count"] == 1
    assert borrower["reserved_by"] is None


async def test_forward_skip_reaches_answered_without_ringing(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    results = await deliver(event_processor, context.provider_call_id, ["ANSWERED"])

    assert results[0].status is EventProcessingStatus.PROCESSED
    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.ANSWERED
    assert call.answered_at is not None


async def test_late_event_after_a_failed_call_is_ignored(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    await deliver(event_processor, context.provider_call_id, ["FAILED"])

    result = await event_processor.process_event(
        make_event(context.provider_call_id, "ANSWERED", "event-late")
    )

    assert result.status is EventProcessingStatus.STALE_IGNORED
    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.FAILED


async def test_duplicate_ringing_with_a_new_event_id_is_stale(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    results = await deliver(event_processor, context.provider_call_id, ["RINGING", "RINGING"])

    assert [result.status for result in results] == [
        EventProcessingStatus.PROCESSED,
        EventProcessingStatus.STALE_IGNORED,
    ]


async def test_happy_path_walks_call_agent_and_borrower_to_completion(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    results = await deliver(
        event_processor,
        context.provider_call_id,
        ["RINGING", "ANSWERED", "CONNECTED", "COMPLETED"],
    )

    assert all(result.status is EventProcessingStatus.PROCESSED for result in results)

    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.COMPLETED
    assert call.ringing_at is not None
    assert call.answered_at is not None
    assert call.connected_at is not None
    assert call.ended_at is not None

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state"] == AgentState.WRAP_UP.value
    assert agent["reserved_by"] is None
    assert agent["current_call_id"] is None

    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})
    assert borrower["status"] == BorrowerStatus.CONTACTED.value
    assert borrower["attempt_count"] == 1


async def test_failure_path_returns_the_agent_and_backs_off_the_borrower(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    await deliver(event_processor, context.provider_call_id, ["RINGING", "FAILED"])

    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.FAILED
    assert call.failure_reason == "provider_reported_failure"

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state"] == AgentState.AVAILABLE.value
    assert agent["reserved_by"] is None

    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["attempt_count"] == 1
    assert borrower["next_eligible_at"] > borrower["last_attempt_at"]


async def test_failure_reason_comes_from_the_event_payload(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    event = ProviderEvent(
        provider_name="mock_a",
        provider_event_id="event-busy",
        provider_call_id=context.provider_call_id,
        event_type="FAILED",
        provider_timestamp=datetime.now(timezone.utc),
        payload={"reason": "busy"},
    )

    await event_processor.process_event(event)

    call = await call_repository.find_by_id(context.call.id)
    assert call.failure_reason == "busy"


async def test_completed_without_an_answer_is_treated_as_a_retry(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    await deliver(event_processor, context.provider_call_id, ["COMPLETED"])

    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["attempt_count"] == 1


async def test_cancelled_call_releases_the_agent(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    await deliver(event_processor, context.provider_call_id, ["CANCELLED"])

    call = await call_repository.find_by_id(context.call.id)
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert call.state is CallState.CANCELLED
    assert agent["state"] == AgentState.AVAILABLE.value
