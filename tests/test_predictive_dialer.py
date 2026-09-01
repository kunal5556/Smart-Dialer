import asyncio

import pytest

from app.models.enums import AgentState, CallState, DialingMode, SafetyVerdict
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

NON_TERMINAL_CALL_STATES = [
    CallState.QUEUED.value,
    CallState.RESERVED.value,
    CallState.INITIATED.value,
    CallState.RINGING.value,
    CallState.ANSWERED.value,
    CallState.CONNECTED.value,
]


async def make_predictive_campaign(test_database, name="Predictive Campaign"):
    campaign = await insert_campaign(test_database, name=name)
    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"dialing_mode": DialingMode.PREDICTIVE.value}}
    )
    return campaign.model_copy(update={"dialing_mode": DialingMode.PREDICTIVE})


def test_predictive_dialer_uses_the_unmodified_engine_config(mode_router, test_settings):
    from app.models.campaign import Campaign

    campaign = Campaign(name="x", dialing_mode=DialingMode.PREDICTIVE)
    config = mode_router.select(campaign).engine_config()

    assert config.forced_answer_rate is None
    assert config.safety_margin == test_settings.SAFETY_MARGIN
    assert config.soon_free_weight == test_settings.SOON_FREE_WEIGHT


async def test_mode_router_selects_by_campaign_mode(test_database, mode_router):
    from app.dialers.predictive_dialer import PredictiveDialer
    from app.dialers.progressive_dialer import ProgressiveDialer

    progressive = await insert_campaign(test_database, name="Progressive")
    predictive = await make_predictive_campaign(test_database)

    assert isinstance(mode_router.select(progressive), ProgressiveDialer)
    assert isinstance(mode_router.select(predictive), PredictiveDialer)


async def test_predictive_over_request_is_reduced_by_the_safety_controller(
    test_database, mode_router
):
    campaign = await make_predictive_campaign(test_database)
    await insert_agents(test_database, campaign.id, 4, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 200)

    result = await mode_router.select(campaign).tick(campaign, "worker-1")

    assert result.requested > result.decision.approved
    assert result.decision.verdict is SafetyVerdict.REDUCED
    assert result.decision.approved == 4
    assert result.allocation.allocated == 4
    assert await test_database["calls"].count_documents({}) == 4


async def test_predictive_never_creates_more_calls_than_agents(test_database, mode_router):
    campaign = await make_predictive_campaign(test_database)
    await insert_agents(test_database, campaign.id, 6, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 500)
    dialer = mode_router.select(campaign)

    for _ in range(4):
        await dialer.tick(campaign, "worker-1")
        bound = await test_database["calls"].count_documents(
            {"state": {"$in": NON_TERMINAL_CALL_STATES}}
        )
        assert bound <= 6

    calls = await test_database["calls"].find(
        {"state": {"$in": NON_TERMINAL_CALL_STATES}}
    ).to_list(None)
    agent_ids = [call["agent_id"] for call in calls]
    assert len(agent_ids) == len(set(agent_ids))


async def test_predictive_requests_more_than_progressive_at_a_low_answer_rate(
    test_database, mode_router
):
    progressive = await insert_campaign(test_database, name="Progressive Compare")
    predictive = await make_predictive_campaign(test_database, name="Predictive Compare")
    for campaign in (progressive, predictive):
        await test_database["campaigns"].update_one(
            {"_id": campaign.id},
            {"$set": {"pacing_config.baseline_answer_rate": 0.2}},
        )
        await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)
        await insert_borrowers(test_database, campaign.id, 500)

    progressive = progressive.model_copy(
        update={"pacing_config": progressive.pacing_config.model_copy(
            update={"baseline_answer_rate": 0.2}
        )}
    )
    predictive = predictive.model_copy(
        update={"pacing_config": predictive.pacing_config.model_copy(
            update={"baseline_answer_rate": 0.2}
        )}
    )

    progressive_result = await mode_router.select(progressive).tick(progressive, "worker-1")
    predictive_result = await mode_router.select(predictive).tick(predictive, "worker-2")

    assert predictive_result.requested > progressive_result.requested


async def test_unhealthy_provider_stops_predictive_dialing(
    test_database, mode_router, health_manager
):
    campaign = await make_predictive_campaign(test_database)
    await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 100)
    for _ in range(10):
        health_manager.record_originate(campaign.provider_name, success=False, latency_ms=10)

    result = await mode_router.select(campaign).tick(campaign, "worker-1")

    assert result.requested == 0
    assert result.decision.approved == 0
    assert await test_database["calls"].count_documents({}) == 0


async def test_concurrent_predictive_workers_stay_within_the_agent_pool(
    test_database, mode_router
):
    campaign = await make_predictive_campaign(test_database)
    await insert_agents(test_database, campaign.id, 8, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 300)
    dialer = mode_router.select(campaign)

    await asyncio.gather(
        *(dialer.tick(campaign, f"worker-{index}") for index in range(3))
    )

    bound = await test_database["calls"].count_documents(
        {"state": {"$in": NON_TERMINAL_CALL_STATES}}
    )
    assert bound <= 8

    agents = await test_database["agents"].find({}).to_list(None)
    reserved = [agent for agent in agents if agent["state"] == AgentState.RESERVED.value]
    assert reserved == []
