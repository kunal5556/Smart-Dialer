import asyncio

from app.repositories.agent_repo import AgentRepository
from loadtest.harness import LoadTestResult, Timer, seed_campaign, summarise

RESERVATION_TTL_SECONDS = 30


async def measure(database, scale: int) -> LoadTestResult:
    campaign = await seed_campaign(database, agents=scale, borrowers=1)
    agents = AgentRepository(database)
    candidates = await agents.find_claimable_agents(campaign.id, needed=scale)
    agent_ids = [agent.id for agent in candidates]

    timer = Timer()

    async def claim(index: int):
        return await timer.measure(
            agents.try_reserve_agent(
                campaign_id=campaign.id,
                agent_id=agent_ids[index % len(agent_ids)],
                worker_id=f"worker-{index}",
                ttl_seconds=RESERVATION_TTL_SECONDS,
            )
        )

    attempts = scale * 2
    started = asyncio.get_running_loop().time()
    outcomes = await asyncio.gather(*(claim(index) for index in range(attempts)))
    duration = asyncio.get_running_loop().time() - started

    winners = [agent for agent in outcomes if agent is not None]
    winner_ids = [agent.id for agent in winners]
    assert len(winner_ids) == len(set(winner_ids)), "an agent was claimed twice"

    reserved = await database["agents"].count_documents({"state": "RESERVED"})
    assert reserved == len(winners)

    return summarise(
        name="agent_reservation",
        scale=scale,
        latencies_ms=timer.latencies_ms,
        duration_seconds=duration,
        extra={
            "attempts": attempts,
            "successful_claims": len(winners),
            "contention_ratio": round(1 - len(winners) / attempts, 3),
            "double_claims": 0,
        },
    )
