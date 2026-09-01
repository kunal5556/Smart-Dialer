import asyncio
from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, CallState, DialingMode
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


async def count_agent_bound_calls(test_database) -> int:
    return await test_database["calls"].count_documents(
        {"state": {"$in": NON_TERMINAL_CALL_STATES}}
    )


async def assert_no_agent_is_double_booked(test_database) -> None:
    calls = await test_database["calls"].find(
        {"state": {"$in": NON_TERMINAL_CALL_STATES}}
    ).to_list(None)
    agent_ids = [call["agent_id"] for call in calls]
    assert len(agent_ids) == len(set(agent_ids))

    borrower_ids = [call["borrower_id"] for call in calls]
    assert len(borrower_ids) == len(set(borrower_ids))


@pytest.mark.parametrize("agent_count", [1, 5, 10, 50])
async def test_agent_bound_calls_never_exceed_available_agents(
    test_database, mode_router, agent_count
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, agent_count, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1000)
    dialer = mode_router.select(campaign)

    for _ in range(4):
        await dialer.tick(campaign, "worker-1")
        assert await count_agent_bound_calls(test_database) <= agent_count
        await assert_no_agent_is_double_booked(test_database)

    assert await count_agent_bound_calls(test_database) == agent_count


async def test_four_concurrent_workers_cannot_exceed_the_agent_count(
    test_database, mode_router
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1000)
    dialer = mode_router.select(campaign)

    await asyncio.gather(
        *(dialer.tick(campaign, f"worker-{index}") for index in range(4))
    )

    assert await count_agent_bound_calls(test_database) <= 10
    await assert_no_agent_is_double_booked(test_database)


async def test_campaign_concurrency_cap_is_respected(test_database, mode_router):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 3})
    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"max_concurrent_calls": 3}}
    )
    await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 100)
    dialer = mode_router.select(campaign)

    for _ in range(3):
        await dialer.tick(campaign, "worker-1")

    assert await count_agent_bound_calls(test_database) <= 3


async def test_progressive_mode_routes_through_the_safety_controller(
    test_database, mode_router
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 4, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 20)

    result = await mode_router.select(campaign).tick(campaign, "worker-1")

    assert result.decision.approved <= 4
    assert await test_database["safety_decisions"].count_documents({}) == 1
    assert await test_database["pacing_decisions"].count_documents({}) == 1


async def test_progressive_dialer_is_selected_for_progressive_campaigns(
    test_database, mode_router
):
    campaign = await insert_campaign(test_database)

    dialer = mode_router.select(campaign)
    config = dialer.engine_config()

    assert campaign.dialing_mode is DialingMode.PROGRESSIVE
    assert config.forced_answer_rate == 1.0
    assert config.safety_margin == 1.0
    assert config.soon_free_weight == 0.0


async def test_no_agents_means_no_calls(test_database, mode_router):
    campaign = await insert_campaign(test_database)
    await insert_borrowers(test_database, campaign.id, 100)

    result = await mode_router.select(campaign).tick(campaign, "worker-1")

    assert result.decision.approved == 0
    assert result.allocation.allocated == 0
    assert await test_database["calls"].count_documents({}) == 0


async def test_no_borrowers_means_no_calls_and_no_stuck_agents(test_database, mode_router):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)

    result = await mode_router.select(campaign).tick(campaign, "worker-1")

    assert result.allocation.allocated == 0
    assert await test_database["calls"].count_documents({}) == 0
    assert (
        await test_database["agents"].count_documents({"state": AgentState.AVAILABLE.value}) == 5
    )


async def test_end_to_end_campaign_completes_calls_with_provider_a(
    test_database, mode_router, fast_provider_registry, wrap_up_service, agent_repository
):
    from app.state_machines.agent_sm import TransitionActor

    campaign = await insert_campaign(test_database)
    provider = fast_provider_registry.get(campaign.provider_name)
    provider.behaviour.ring_duration = 0.0
    provider.behaviour.avg_talk_time = 0.0
    provider.behaviour.answer_rate = 1.0

    await insert_agents(test_database, campaign.id, 4, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 4)

    await mode_router.select(campaign).tick(campaign, "worker-1")
    for _ in range(50):
        await asyncio.sleep(0.01)
        completed = await test_database["calls"].count_documents(
            {"state": CallState.COMPLETED.value}
        )
        if completed == 4:
            break

    assert await test_database["calls"].count_documents(
        {"state": CallState.COMPLETED.value}
    ) == 4
    assert await test_database["borrowers"].count_documents({"status": "CONTACTED"}) == 4
    assert await test_database["agents"].count_documents(
        {"state": AgentState.WRAP_UP.value}
    ) == 4

    await test_database["agents"].update_many(
        {}, {"$set": {"state_changed_at": utc_now() - timedelta(seconds=3600)}}
    )
    for agent in await wrap_up_service.find_finished_wrap_ups(campaign.id):
        await agent_repository.transition_agent(
            agent_id=agent.id,
            from_state=AgentState.WRAP_UP,
            to_state=AgentState.AVAILABLE,
            actor=TransitionActor.WORKER_TIMER,
            expected_version=agent.state_version,
        )

    assert await test_database["agents"].count_documents(
        {"state": AgentState.AVAILABLE.value}
    ) == 4
