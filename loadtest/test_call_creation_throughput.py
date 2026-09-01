import asyncio

from app.repositories.call_repo import CallRepository
from loadtest.harness import LoadTestResult, Timer, seed_campaign, summarise


async def measure(database, scale: int) -> LoadTestResult:
    campaign = await seed_campaign(database, agents=1, borrowers=1)
    calls = CallRepository(database)
    timer = Timer()

    async def create(index: int):
        return await timer.measure(
            calls.create_call(
                campaign_id=campaign.id,
                agent_id=f"agent-{index}",
                borrower_id=f"borrower-{index}",
                provider_name="mock_a",
                worker_id=f"worker-{index}",
            )
        )

    started = asyncio.get_running_loop().time()
    await asyncio.gather(*(create(index) for index in range(scale)))
    duration = asyncio.get_running_loop().time() - started

    stored = await database["calls"].count_documents({})
    assert stored == scale

    return summarise(
        name="call_creation",
        scale=scale,
        latencies_ms=timer.latencies_ms,
        duration_seconds=duration,
        extra={"calls_stored": stored},
    )
