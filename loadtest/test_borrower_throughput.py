import asyncio

from app.repositories.borrower_repo import BorrowerRepository
from loadtest.harness import LoadTestResult, Timer, seed_campaign, summarise

RESERVATION_TTL_SECONDS = 30


async def measure(database, scale: int) -> LoadTestResult:
    campaign = await seed_campaign(database, agents=1, borrowers=scale)
    borrowers = BorrowerRepository(database)
    candidates = await borrowers.find_claimable_borrowers(campaign.id, needed=scale)
    borrower_ids = [borrower.id for borrower in candidates]

    timer = Timer()

    async def claim(index: int):
        return await timer.measure(
            borrowers.try_reserve_borrower(
                campaign_id=campaign.id,
                borrower_id=borrower_ids[index % len(borrower_ids)],
                worker_id=f"worker-{index}",
                ttl_seconds=RESERVATION_TTL_SECONDS,
            )
        )

    attempts = scale * 2
    started = asyncio.get_running_loop().time()
    outcomes = await asyncio.gather(*(claim(index) for index in range(attempts)))
    duration = asyncio.get_running_loop().time() - started

    winners = [borrower for borrower in outcomes if borrower is not None]
    winner_ids = [borrower.id for borrower in winners]
    assert len(winner_ids) == len(set(winner_ids)), "a borrower was claimed twice"

    return summarise(
        name="borrower_reservation",
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
