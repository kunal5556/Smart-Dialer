from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, BorrowerStatus, CallState, DialingMode, SafetyVerdict
from app.safety.models import PacingRequest
from app.safety.safety_controller import CONSTRAINT_AVAILABILITY_DROP
from tests.conftest import insert_agents, insert_campaign, prepare_dialing_call

pytestmark = pytest.mark.usefixtures("clean_call_collections")

AGENT_COUNT = 100
DISAPPEARING = 40


def make_request(requested: int) -> PacingRequest:
    return PacingRequest(
        requested=requested,
        mode=DialingMode.PREDICTIVE,
        snapshot_captured_at=utc_now(),
        inputs={},
        explanation="test",
    )


async def stop_heartbeats(test_database, agent_ids, test_settings) -> None:
    expired = utc_now() - timedelta(
        seconds=test_settings.AGENT_HEARTBEAT_TIMEOUT_SECONDS + 60
    )
    await test_database["agents"].update_many(
        {"_id": {"$in": agent_ids}}, {"$set": {"last_heartbeat_at": expired}}
    )


async def test_agents_that_stop_heartbeating_are_taken_offline(
    test_database, recovery_worker, test_settings
):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(
        test_database, campaign.id, AGENT_COUNT, state=AgentState.AVAILABLE
    )
    await test_database["agents"].update_many({}, {"$set": {"last_heartbeat_at": utc_now()}})
    disappearing = [agent.id for agent in agents[:DISAPPEARING]]
    await stop_heartbeats(test_database, disappearing, test_settings)

    counts = await recovery_worker.run_sweeps()

    assert counts.heartbeat_timeouts == DISAPPEARING
    offline = await test_database["agents"].count_documents(
        {"state": AgentState.OFFLINE.value}
    )
    available = await test_database["agents"].count_documents(
        {"state": AgentState.AVAILABLE.value}
    )
    assert offline == DISAPPEARING
    assert available == AGENT_COUNT - DISAPPEARING


async def test_agents_that_still_heartbeat_are_untouched(
    test_database, recovery_worker
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)
    await test_database["agents"].update_many({}, {"$set": {"last_heartbeat_at": utc_now()}})

    counts = await recovery_worker.run_sweeps()

    assert counts.heartbeat_timeouts == 0


async def test_agents_that_never_heartbeat_are_not_forced_offline(
    test_database, recovery_worker
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)

    counts = await recovery_worker.run_sweeps()

    assert counts.heartbeat_timeouts == 0
    assert (
        await test_database["agents"].count_documents({"state": AgentState.AVAILABLE.value}) == 5
    )


async def test_a_disappearing_agent_has_its_call_cancelled_and_borrower_released(
    test_database, recovery_worker, call_repository, test_settings
):
    context = await prepare_dialing_call(test_database, call_repository)
    await test_database["agents"].update_one(
        {"_id": context.agent.id}, {"$set": {"current_call_id": context.call.id}}
    )
    await stop_heartbeats(test_database, [context.agent.id], test_settings)

    await recovery_worker.run_sweeps()

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    call = await call_repository.find_by_id(context.call.id)
    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})

    assert agent["state"] == AgentState.OFFLINE.value
    assert call.state is CallState.CANCELLED
    assert call.failure_reason == "agent_disappeared"
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["reserved_by"] is None


async def test_safety_capacity_reflects_the_drop_within_one_tick(
    test_database, recovery_worker, safety_controller, test_settings
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    agents = await insert_agents(
        test_database, campaign.id, AGENT_COUNT, state=AgentState.AVAILABLE
    )
    await test_database["agents"].update_many({}, {"$set": {"last_heartbeat_at": utc_now()}})

    before = await safety_controller.evaluate(campaign, make_request(200))
    assert before.approved == AGENT_COUNT

    await stop_heartbeats(test_database, [a.id for a in agents[:DISAPPEARING]], test_settings)
    await recovery_worker.run_sweeps()

    after = await safety_controller.evaluate(campaign, make_request(200))

    assert after.approved == AGENT_COUNT - DISAPPEARING
    assert after.verdict is SafetyVerdict.FALLBACK_PROGRESSIVE
    assert after.fallback_reason == "availability_drop"


async def test_availability_drop_constraint_is_named_in_the_decision(
    test_database, safety_controller, availability_tracker
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    agents = await insert_agents(test_database, campaign.id, 100, state=AgentState.AVAILABLE)

    await safety_controller.evaluate(campaign, make_request(1))
    await test_database["agents"].update_many(
        {"_id": {"$in": [a.id for a in agents[:60]]}},
        {"$set": {"state": AgentState.OFFLINE.value}},
    )
    decision = await safety_controller.evaluate(campaign, make_request(500))

    limits = {item.name: item.limit for item in decision.constraints}
    assert limits[CONSTRAINT_AVAILABILITY_DROP] == 40
    assert decision.approved == 40


async def test_a_small_dip_does_not_trigger_fallback(
    test_database, safety_controller, availability_tracker
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    agents = await insert_agents(test_database, campaign.id, 100, state=AgentState.AVAILABLE)

    await safety_controller.evaluate(campaign, make_request(1))
    await test_database["agents"].update_many(
        {"_id": {"$in": [a.id for a in agents[:5]]}},
        {"$set": {"state": AgentState.OFFLINE.value}},
    )
    decision = await safety_controller.evaluate(campaign, make_request(50))

    assert decision.fallback_reason is None
    assert decision.approved == 50


def test_tracker_reports_the_drop_ratio(availability_tracker):
    assert availability_tracker.record_and_detect("campaign-1", 100) is None

    drop = availability_tracker.record_and_detect("campaign-1", 60)

    assert drop is not None
    assert drop.previous_available == 100
    assert drop.current_available == 60
    assert drop.drop_ratio == pytest.approx(0.4)


def test_tracker_ignores_growth(availability_tracker):
    availability_tracker.record_and_detect("campaign-1", 50)

    assert availability_tracker.record_and_detect("campaign-1", 80) is None


def test_tracker_keeps_campaigns_independent(availability_tracker):
    availability_tracker.record_and_detect("campaign-a", 100)
    availability_tracker.record_and_detect("campaign-b", 10)

    assert availability_tracker.record_and_detect("campaign-a", 20) is not None
    assert availability_tracker.record_and_detect("campaign-b", 10) is None
