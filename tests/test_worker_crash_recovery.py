from datetime import datetime, timedelta, timezone

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, BorrowerStatus, CallState, SafetyVerdict
from app.providers.base import ProviderEvent
from app.safety.models import SafetyDecision
from tests.conftest import insert_agents, insert_borrowers, insert_campaign, prepare_dialing_call

pytestmark = pytest.mark.usefixtures("clean_call_collections")


def approve(campaign_id: str, approved: int) -> SafetyDecision:
    return SafetyDecision(
        campaign_id=campaign_id,
        requested=approved,
        approved=approved,
        verdict=SafetyVerdict.APPROVED,
        constraints=[],
        binding_constraint=None,
        snapshot_age_ms=0,
        created_at=utc_now(),
    )


def make_event(provider_call_id: str, event_type: str, event_id: str) -> ProviderEvent:
    return ProviderEvent(
        provider_name="mock_a",
        provider_event_id=event_id,
        provider_call_id=provider_call_id,
        event_type=event_type,
        provider_timestamp=datetime.now(timezone.utc),
    )


async def crash_worker(test_database, context) -> None:
    expired = utc_now() - timedelta(seconds=120)
    await test_database["agents"].update_one(
        {"_id": context.agent.id},
        {"$set": {"lease_expires_at": expired, "current_call_id": context.call.id}},
    )
    await test_database["borrowers"].update_one(
        {"_id": context.borrower.id}, {"$set": {"lease_expires_at": expired}}
    )


async def test_crash_after_initiation_recovers_every_party(
    test_database, recovery_worker, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    await crash_worker(test_database, context)

    await recovery_worker.run_sweeps()

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})
    call = await call_repository.find_by_id(context.call.id)

    assert agent["state"] == AgentState.AVAILABLE.value
    assert agent["reserved_by"] is None
    assert agent["current_call_id"] is None
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["attempt_count"] == 1
    assert call.terminal is True


async def test_recovery_creates_no_duplicate_call_on_the_next_tick(
    test_database, recovery_worker, call_repository, call_allocator
):
    context = await prepare_dialing_call(test_database, call_repository)
    await crash_worker(test_database, context)
    await recovery_worker.run_sweeps()

    await test_database["borrowers"].update_one(
        {"_id": context.borrower.id}, {"$set": {"next_eligible_at": utc_now()}}
    )
    campaign = context.campaign
    result = await call_allocator.allocate(campaign, approve(campaign.id, 1), "worker-2")

    calls = await test_database["calls"].find({}).to_list(None)
    keys = [call["idempotency_key"] for call in calls]

    assert len(keys) == len(set(keys))
    assert result.allocated == 1
    assert len(calls) == 2
    assert calls[1]["attempt"] == 2


async def test_crash_after_answered_still_processes_the_later_completed_event(
    test_database, recovery_worker, call_repository, event_processor
):
    context = await prepare_dialing_call(test_database, call_repository)

    answered = await event_processor.process_event(
        make_event(context.provider_call_id, "ANSWERED", "event-answered")
    )
    assert answered.status.value == "PROCESSED"

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state"] == AgentState.CONNECTED.value

    completed = await event_processor.process_event(
        make_event(context.provider_call_id, "COMPLETED", "event-completed")
    )

    assert completed.status.value == "PROCESSED"
    call = await call_repository.find_by_id(context.call.id)
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})

    assert call.state is CallState.COMPLETED
    assert agent["state"] == AgentState.WRAP_UP.value
    assert borrower["status"] == BorrowerStatus.CONTACTED.value

    await test_database["agents"].update_one(
        {"_id": context.agent.id},
        {"$set": {"state_changed_at": utc_now() - timedelta(hours=1)}},
    )
    counts = await recovery_worker.run_sweeps()

    assert counts.stuck_wrap_ups == 1
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state"] == AgentState.AVAILABLE.value


async def test_no_reservation_survives_a_crash_indefinitely(
    test_database, recovery_worker, call_allocator
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 5)
    await call_allocator.allocate(campaign, approve(campaign.id, 5), "doomed-worker")

    expired = utc_now() - timedelta(seconds=120)
    await test_database["agents"].update_many({}, {"$set": {"lease_expires_at": expired}})
    await test_database["borrowers"].update_many({}, {"$set": {"lease_expires_at": expired}})

    await recovery_worker.run_sweeps()

    stuck_agents = await test_database["agents"].count_documents(
        {"state": AgentState.RESERVED.value}
    )
    stuck_borrowers = await test_database["borrowers"].count_documents(
        {"status": BorrowerStatus.RESERVED.value}
    )

    assert stuck_agents == 0
    assert stuck_borrowers == 0
