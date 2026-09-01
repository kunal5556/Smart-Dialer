import asyncio

import pytest

from app.models.enums import AgentState
from tests.conftest import insert_agents, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_reservation_collections")

RESERVATION_TTL_SECONDS = 30


async def test_twenty_workers_race_for_one_agent(test_database, agent_repository):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    agent_id = agents[0].id

    results = await asyncio.gather(
        *(
            agent_repository.try_reserve_agent(
                campaign_id=campaign.id,
                agent_id=agent_id,
                worker_id=f"worker-{index}",
                ttl_seconds=RESERVATION_TTL_SECONDS,
            )
            for index in range(20)
        )
    )

    winners = [agent for agent in results if agent is not None]
    assert len(winners) == 1
    assert results.count(None) == 19

    stored = await test_database["agents"].find_one({"_id": agent_id})
    assert stored["state"] == AgentState.RESERVED.value
    assert stored["reserved_by"] == winners[0].reserved_by
    assert stored["state_version"] == 1
    assert stored["lease_expires_at"] > stored["reserved_at"]


async def test_ten_agents_and_fifty_workers_produce_ten_distinct_winners(
    test_database, agent_repository
):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)
    agent_ids = [agent.id for agent in agents]

    async def claim_one(worker_index: int):
        for agent_id in agent_ids:
            agent = await agent_repository.try_reserve_agent(
                campaign_id=campaign.id,
                agent_id=agent_id,
                worker_id=f"worker-{worker_index}",
                ttl_seconds=RESERVATION_TTL_SECONDS,
            )
            if agent is not None:
                return agent
        return None

    results = await asyncio.gather(*(claim_one(index) for index in range(50)))

    winners = [agent for agent in results if agent is not None]
    assert len(winners) == 10
    assert len({agent.id for agent in winners}) == 10
    assert len({agent.reserved_by for agent in winners}) == 10

    reserved = await test_database["agents"].count_documents(
        {"state": AgentState.RESERVED.value}
    )
    assert reserved == 10
    for agent_document in await test_database["agents"].find({}).to_list(None):
        assert agent_document["state_version"] == 1


async def test_reserving_an_agent_that_is_not_available_fails(test_database, agent_repository):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 1, state=AgentState.OFFLINE)

    reserved = await agent_repository.try_reserve_agent(
        campaign_id=campaign.id,
        agent_id=agents[0].id,
        worker_id="worker-1",
        ttl_seconds=RESERVATION_TTL_SECONDS,
    )

    assert reserved is None


async def test_reserving_an_agent_from_another_campaign_fails(test_database, agent_repository):
    campaign = await insert_campaign(test_database, name="Campaign A")
    other_campaign = await insert_campaign(test_database, name="Campaign B")
    agents = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)

    reserved = await agent_repository.try_reserve_agent(
        campaign_id=other_campaign.id,
        agent_id=agents[0].id,
        worker_id="worker-1",
        ttl_seconds=RESERVATION_TTL_SECONDS,
    )

    assert reserved is None


async def test_find_claimable_agents_widens_the_candidate_window(test_database, agent_repository):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 20, state=AgentState.AVAILABLE)

    candidates = await agent_repository.find_claimable_agents(campaign.id, needed=2)

    assert len(candidates) == 6


async def test_find_claimable_agents_returns_only_available_agents(
    test_database, agent_repository
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_agents(test_database, campaign.id, 4, state=AgentState.OFFLINE)

    candidates = await agent_repository.find_claimable_agents(campaign.id, needed=10)

    assert len(candidates) == 3
    assert all(agent.state is AgentState.AVAILABLE for agent in candidates)


async def test_count_by_state_reports_every_state(test_database, agent_repository):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.OFFLINE)

    counts = await agent_repository.count_by_state(campaign.id)

    assert set(counts) == set(AgentState)
    assert counts[AgentState.AVAILABLE] == 3
    assert counts[AgentState.OFFLINE] == 2
    assert counts[AgentState.CONNECTED] == 0


async def test_heartbeat_records_the_timestamp(test_database, agent_repository):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 1)

    assert await agent_repository.heartbeat(agents[0].id) is True

    stored = await test_database["agents"].find_one({"_id": agents[0].id})
    assert stored["last_heartbeat_at"] is not None


async def test_heartbeat_for_an_unknown_agent_reports_failure(agent_repository):
    assert await agent_repository.heartbeat("missing-agent") is False
