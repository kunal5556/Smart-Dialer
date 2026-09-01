import asyncio
from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.enums import BorrowerStatus
from app.repositories.borrower_repo import BorrowerReleaseOutcome
from tests.conftest import insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_reservation_collections")

RESERVATION_TTL_SECONDS = 30
MAX_CALL_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 60


async def test_twenty_workers_race_for_one_borrower(test_database, borrower_repository):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 1)
    borrower_id = borrowers[0].id

    results = await asyncio.gather(
        *(
            borrower_repository.try_reserve_borrower(
                campaign_id=campaign.id,
                borrower_id=borrower_id,
                worker_id=f"worker-{index}",
                ttl_seconds=RESERVATION_TTL_SECONDS,
            )
            for index in range(20)
        )
    )

    winners = [borrower for borrower in results if borrower is not None]
    assert len(winners) == 1
    assert results.count(None) == 19

    stored = await test_database["borrowers"].find_one({"_id": borrower_id})
    assert stored["status"] == BorrowerStatus.RESERVED.value
    assert stored["reserved_by"] == winners[0].reserved_by
    assert stored["state_version"] == 1


async def test_ten_borrowers_and_fifty_workers_produce_ten_distinct_winners(
    test_database, borrower_repository
):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 10)
    borrower_ids = [borrower.id for borrower in borrowers]

    async def claim_one(worker_index: int):
        for borrower_id in borrower_ids:
            borrower = await borrower_repository.try_reserve_borrower(
                campaign_id=campaign.id,
                borrower_id=borrower_id,
                worker_id=f"worker-{worker_index}",
                ttl_seconds=RESERVATION_TTL_SECONDS,
            )
            if borrower is not None:
                return borrower
        return None

    results = await asyncio.gather(*(claim_one(index) for index in range(50)))

    winners = [borrower for borrower in results if borrower is not None]
    assert len(winners) == 10
    assert len({borrower.id for borrower in winners}) == 10


async def test_borrower_in_backoff_is_not_claimable(test_database, borrower_repository):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 1)
    await test_database["borrowers"].update_one(
        {"_id": borrowers[0].id},
        {"$set": {"next_eligible_at": utc_now() + timedelta(minutes=5)}},
    )

    reserved = await borrower_repository.try_reserve_borrower(
        campaign_id=campaign.id,
        borrower_id=borrowers[0].id,
        worker_id="worker-1",
        ttl_seconds=RESERVATION_TTL_SECONDS,
    )
    candidates = await borrower_repository.find_claimable_borrowers(campaign.id, needed=5)

    assert reserved is None
    assert candidates == []


async def test_release_without_an_attempt_returns_the_borrower_to_pending(
    test_database, borrower_repository
):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 1)
    await borrower_repository.try_reserve_borrower(
        campaign_id=campaign.id,
        borrower_id=borrowers[0].id,
        worker_id="worker-1",
        ttl_seconds=RESERVATION_TTL_SECONDS,
    )

    released = await borrower_repository.release_borrower(
        borrower_id=borrowers[0].id,
        worker_id="worker-1",
        outcome=BorrowerReleaseOutcome.RELEASED,
        max_attempts=MAX_CALL_ATTEMPTS,
        backoff_base_seconds=BACKOFF_BASE_SECONDS,
    )

    assert released.status is BorrowerStatus.PENDING
    assert released.attempt_count == 0
    assert released.reserved_by is None


async def test_failed_attempts_apply_increasing_backoff_then_exhaust(
    test_database, borrower_repository
):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 1)
    borrower_id = borrowers[0].id

    backoff_delays = []
    for attempt in range(1, MAX_CALL_ATTEMPTS + 1):
        await test_database["borrowers"].update_one(
            {"_id": borrower_id}, {"$set": {"next_eligible_at": utc_now()}}
        )
        reserved = await borrower_repository.try_reserve_borrower(
            campaign_id=campaign.id,
            borrower_id=borrower_id,
            worker_id="worker-1",
            ttl_seconds=RESERVATION_TTL_SECONDS,
        )
        assert reserved is not None

        released = await borrower_repository.release_borrower(
            borrower_id=borrower_id,
            worker_id="worker-1",
            outcome=BorrowerReleaseOutcome.RETRY,
            max_attempts=MAX_CALL_ATTEMPTS,
            backoff_base_seconds=BACKOFF_BASE_SECONDS,
        )

        assert released.attempt_count == attempt
        assert released.last_attempt_at is not None
        backoff_delays.append(released.next_eligible_at - released.last_attempt_at)

        if attempt < MAX_CALL_ATTEMPTS:
            assert released.status is BorrowerStatus.PENDING
        else:
            assert released.status is BorrowerStatus.EXHAUSTED

    assert backoff_delays == [
        timedelta(seconds=BACKOFF_BASE_SECONDS),
        timedelta(seconds=BACKOFF_BASE_SECONDS * 2),
        timedelta(seconds=BACKOFF_BASE_SECONDS * 4),
    ]


async def test_exhausted_borrower_is_never_claimable_again(
    test_database, borrower_repository
):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 1)
    borrower_id = borrowers[0].id

    for _ in range(MAX_CALL_ATTEMPTS):
        await test_database["borrowers"].update_one(
            {"_id": borrower_id}, {"$set": {"next_eligible_at": utc_now()}}
        )
        await borrower_repository.try_reserve_borrower(
            campaign_id=campaign.id,
            borrower_id=borrower_id,
            worker_id="worker-1",
            ttl_seconds=RESERVATION_TTL_SECONDS,
        )
        await borrower_repository.release_borrower(
            borrower_id=borrower_id,
            worker_id="worker-1",
            outcome=BorrowerReleaseOutcome.RETRY,
            max_attempts=MAX_CALL_ATTEMPTS,
            backoff_base_seconds=BACKOFF_BASE_SECONDS,
        )

    await test_database["borrowers"].update_one(
        {"_id": borrower_id}, {"$set": {"next_eligible_at": utc_now()}}
    )
    reserved = await borrower_repository.try_reserve_borrower(
        campaign_id=campaign.id,
        borrower_id=borrower_id,
        worker_id="worker-2",
        ttl_seconds=RESERVATION_TTL_SECONDS,
    )

    assert reserved is None
