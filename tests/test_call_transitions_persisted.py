import asyncio
from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.call import build_idempotency_key
from app.models.enums import CallState
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


async def create_call(call_repository, campaign_id, agent_id, borrower_id, attempt=1):
    return await call_repository.create_call(
        campaign_id=campaign_id,
        agent_id=agent_id,
        borrower_id=borrower_id,
        provider_name="mock_a",
        worker_id="worker-1",
        attempt=attempt,
    )


@pytest.fixture
async def campaign_with_parties(test_database):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)
    [borrower] = await insert_borrowers(test_database, campaign.id, 1)
    return campaign, agent, borrower


async def test_create_call_builds_the_documented_idempotency_key(
    call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties

    call = await create_call(call_repository, campaign.id, agent.id, borrower.id)

    assert call.idempotency_key == build_idempotency_key(campaign.id, agent.id, borrower.id, 1)
    assert call.state is CallState.QUEUED
    assert call.terminal is False


async def test_recreating_the_same_call_returns_the_existing_one(
    test_database, call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties

    first = await create_call(call_repository, campaign.id, agent.id, borrower.id)
    second = await create_call(call_repository, campaign.id, agent.id, borrower.id)

    assert first.id == second.id
    assert await test_database["calls"].count_documents({}) == 1


async def test_a_new_attempt_creates_a_separate_call(
    test_database, call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties

    first = await create_call(call_repository, campaign.id, agent.id, borrower.id, attempt=1)
    second = await create_call(call_repository, campaign.id, agent.id, borrower.id, attempt=2)

    assert first.id != second.id
    assert await test_database["calls"].count_documents({}) == 2


async def test_transition_call_records_rank_terminal_and_timestamps(
    call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties
    call = await create_call(call_repository, campaign.id, agent.id, borrower.id)

    ringing = await call_repository.transition_call(call.id, CallState.RINGING)
    completed = await call_repository.transition_call(ringing.id, CallState.COMPLETED)

    assert ringing.state_rank == 3
    assert ringing.ringing_at is not None
    assert ringing.terminal is False
    assert completed.terminal is True
    assert completed.ended_at is not None


async def test_transition_call_refuses_to_move_backwards(
    call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties
    call = await create_call(call_repository, campaign.id, agent.id, borrower.id)
    await call_repository.transition_call(call.id, CallState.CONNECTED)

    result = await call_repository.transition_call(call.id, CallState.RINGING)

    assert result is None
    stored = await call_repository.find_by_id(call.id)
    assert stored.state is CallState.CONNECTED


async def test_transition_call_refuses_to_leave_a_terminal_state(
    call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties
    call = await create_call(call_repository, campaign.id, agent.id, borrower.id)
    await call_repository.transition_call(call.id, CallState.FAILED)

    result = await call_repository.transition_call(call.id, CallState.COMPLETED)

    assert result is None
    stored = await call_repository.find_by_id(call.id)
    assert stored.state is CallState.FAILED


async def test_concurrent_identical_transitions_apply_once(
    call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties
    call = await create_call(call_repository, campaign.id, agent.id, borrower.id)

    results = await asyncio.gather(
        *(call_repository.transition_call(call.id, CallState.RINGING) for _ in range(10))
    )

    applied = [result for result in results if result is not None]
    assert len(applied) == 1


async def test_attach_provider_call_id_only_applies_once(
    call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties
    call = await create_call(call_repository, campaign.id, agent.id, borrower.id)

    first = await call_repository.attach_provider_call_id(call.id, "provider-call-1")
    second = await call_repository.attach_provider_call_id(call.id, "provider-call-2")

    assert first.provider_call_id == "provider-call-1"
    assert second is None


async def test_find_by_provider_call_id(call_repository, campaign_with_parties):
    campaign, agent, borrower = campaign_with_parties
    call = await create_call(call_repository, campaign.id, agent.id, borrower.id)
    await call_repository.attach_provider_call_id(call.id, "provider-call-9")

    found = await call_repository.find_by_provider_call_id("mock_a", "provider-call-9")
    missing = await call_repository.find_by_provider_call_id("mock_b", "provider-call-9")

    assert found.id == call.id
    assert missing is None


async def test_find_stale_calls_returns_only_old_non_terminal_calls(
    test_database, call_repository, campaign_with_parties
):
    campaign, agent, borrower = campaign_with_parties
    stale = await create_call(call_repository, campaign.id, agent.id, borrower.id, attempt=1)
    fresh = await create_call(call_repository, campaign.id, agent.id, borrower.id, attempt=2)
    finished = await create_call(call_repository, campaign.id, agent.id, borrower.id, attempt=3)
    await call_repository.transition_call(finished.id, CallState.COMPLETED)

    old_timestamp = utc_now() - timedelta(minutes=10)
    await test_database["calls"].update_one(
        {"_id": stale.id}, {"$set": {"updated_at": old_timestamp}}
    )
    await test_database["calls"].update_one(
        {"_id": finished.id}, {"$set": {"updated_at": old_timestamp}}
    )

    results = await call_repository.find_stale_calls(
        older_than=utc_now() - timedelta(minutes=5), limit=10
    )

    assert [call.id for call in results] == [stale.id]
    assert fresh.id not in [call.id for call in results]


async def test_count_by_state_reports_every_state(call_repository, campaign_with_parties):
    campaign, agent, borrower = campaign_with_parties
    first = await create_call(call_repository, campaign.id, agent.id, borrower.id, attempt=1)
    await create_call(call_repository, campaign.id, agent.id, borrower.id, attempt=2)
    await call_repository.transition_call(first.id, CallState.RINGING)

    counts = await call_repository.count_by_state(campaign.id)

    assert set(counts) == set(CallState)
    assert counts[CallState.QUEUED] == 1
    assert counts[CallState.RINGING] == 1
    assert counts[CallState.COMPLETED] == 0
