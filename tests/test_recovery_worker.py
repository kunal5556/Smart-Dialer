from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, BorrowerStatus, CallState
from tests.conftest import insert_agents, insert_borrowers, insert_campaign, prepare_dialing_call

pytestmark = pytest.mark.usefixtures("clean_call_collections")


async def expire_lease(test_database, collection: str, document_id: str) -> None:
    await test_database[collection].update_one(
        {"_id": document_id},
        {"$set": {"lease_expires_at": utc_now() - timedelta(seconds=60)}},
    )


async def test_expired_agent_lease_is_reclaimed(
    test_database, recovery_worker, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    await expire_lease(test_database, "agents", context.agent.id)

    counts = await recovery_worker.run_sweeps()

    assert counts.expired_agent_leases == 1
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state"] == AgentState.AVAILABLE.value
    assert agent["reserved_by"] is None
    assert agent["lease_expires_at"] is None


async def test_unexpired_leases_are_untouched(
    test_database, recovery_worker, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    counts = await recovery_worker.run_sweeps()

    assert counts.expired_agent_leases == 0
    assert counts.expired_borrower_leases == 0
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state"] == AgentState.DIALING.value
    assert agent["reserved_by"] == context.worker_id


async def test_bound_call_is_cancelled_before_the_agent_is_freed(
    test_database, recovery_worker, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    await test_database["agents"].update_one(
        {"_id": context.agent.id}, {"$set": {"current_call_id": context.call.id}}
    )
    await expire_lease(test_database, "agents", context.agent.id)

    await recovery_worker.run_sweeps()

    call = await call_repository.find_by_id(context.call.id)
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})

    assert call.state is CallState.CANCELLED
    assert call.failure_reason == "agent_lease_expired"
    assert agent["state"] == AgentState.AVAILABLE.value
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["reserved_by"] is None


async def test_expired_borrower_lease_is_reclaimed(
    test_database, recovery_worker, borrower_repository
):
    campaign = await insert_campaign(test_database)
    [borrower] = await insert_borrowers(test_database, campaign.id, 1)
    reserved = await borrower_repository.try_reserve_borrower(
        campaign_id=campaign.id,
        borrower_id=borrower.id,
        worker_id="dead-worker",
        ttl_seconds=30,
    )
    assert reserved is not None
    await expire_lease(test_database, "borrowers", borrower.id)

    counts = await recovery_worker.run_sweeps()

    assert counts.expired_borrower_leases == 1
    stored = await test_database["borrowers"].find_one({"_id": borrower.id})
    assert stored["status"] == BorrowerStatus.PENDING.value
    assert stored["reserved_by"] is None


async def test_orphaned_call_is_reconciled_against_the_provider(
    test_database, recovery_worker, call_repository, test_settings
):
    context = await prepare_dialing_call(test_database, call_repository)
    stale = utc_now() - timedelta(seconds=test_settings.CALL_STALE_TIMEOUT_SECONDS + 60)
    await test_database["calls"].update_one(
        {"_id": context.call.id}, {"$set": {"updated_at": stale}}
    )

    counts = await recovery_worker.run_sweeps()

    assert counts.orphaned_calls == 1
    call = await call_repository.find_by_id(context.call.id)
    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})

    assert call.state is CallState.FAILED
    assert call.failure_reason == "orphaned"
    assert agent["state"] == AgentState.AVAILABLE.value
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["attempt_count"] == 1


async def test_fresh_calls_are_not_treated_as_orphans(
    test_database, recovery_worker, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    counts = await recovery_worker.run_sweeps()

    assert counts.orphaned_calls == 0
    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.INITIATED


async def test_stuck_wrap_up_is_released(test_database, recovery_worker, test_settings):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)
    grace = test_settings.WRAP_UP_SECONDS * 3 + 10
    await test_database["agents"].update_one(
        {"_id": agent.id},
        {
            "$set": {
                "state": AgentState.WRAP_UP.value,
                "state_changed_at": utc_now() - timedelta(seconds=grace),
            }
        },
    )

    counts = await recovery_worker.run_sweeps()

    assert counts.stuck_wrap_ups == 1
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.AVAILABLE.value


async def test_recent_wrap_up_is_left_to_the_dialer(
    test_database, recovery_worker, test_settings
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)
    await test_database["agents"].update_one(
        {"_id": agent.id},
        {
            "$set": {
                "state": AgentState.WRAP_UP.value,
                "state_changed_at": utc_now()
                - timedelta(seconds=test_settings.WRAP_UP_SECONDS + 1),
            }
        },
    )

    counts = await recovery_worker.run_sweeps()

    assert counts.stuck_wrap_ups == 0
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.WRAP_UP.value


async def test_recovery_is_idempotent(test_database, recovery_worker, call_repository):
    context = await prepare_dialing_call(test_database, call_repository)
    await expire_lease(test_database, "agents", context.agent.id)
    await expire_lease(test_database, "borrowers", context.borrower.id)

    first = await recovery_worker.run_sweeps()
    borrower_after_first = await test_database["borrowers"].find_one(
        {"_id": context.borrower.id}
    )
    second = await recovery_worker.run_sweeps()
    borrower_after_second = await test_database["borrowers"].find_one(
        {"_id": context.borrower.id}
    )

    assert first.expired_agent_leases == 1
    assert second.expired_agent_leases == 0
    assert borrower_after_first["attempt_count"] == borrower_after_second["attempt_count"]
    assert borrower_after_first["state_version"] == borrower_after_second["state_version"]


async def test_a_failing_sweep_does_not_stop_the_others(
    test_database, recovery_worker, call_repository, monkeypatch
):
    context = await prepare_dialing_call(test_database, call_repository)
    await expire_lease(test_database, "borrowers", context.borrower.id)

    async def explode(*args, **kwargs):
        raise RuntimeError("deliberate sweep failure")

    monkeypatch.setattr(recovery_worker, "_sweep_expired_agent_leases", explode)

    counts = await recovery_worker.run_sweeps()

    assert counts.errors == ["expired_agent_leases"]
    assert counts.expired_borrower_leases == 1


async def test_sweeps_are_bounded_by_the_configured_limit(
    test_database, recovery_worker, borrower_repository, test_settings
):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 6)
    for borrower in borrowers:
        await borrower_repository.try_reserve_borrower(
            campaign_id=campaign.id,
            borrower_id=borrower.id,
            worker_id="dead-worker",
            ttl_seconds=30,
        )
        await expire_lease(test_database, "borrowers", borrower.id)
    test_settings.RECOVERY_SWEEP_LIMIT = 2

    counts = await recovery_worker.run_sweeps()

    assert counts.expired_borrower_leases == 2


async def test_worker_loop_starts_and_stops_cleanly(recovery_worker):
    recovery_worker.start()
    recovery_worker.start()
    await recovery_worker.stop()
    await recovery_worker.stop()
