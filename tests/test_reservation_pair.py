import asyncio

import pytest

from app.models.enums import AgentState, BorrowerStatus
from app.repositories.borrower_repo import BorrowerReleaseOutcome
from app.services.reservation_service import ReservationService
from app.workers.worker_identity import build_worker_id, get_worker_id
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_reservation_collections")


@pytest.fixture
def reservation_service(agent_repository, borrower_repository, test_settings):
    return ReservationService(agent_repository, borrower_repository, test_settings)


async def test_reserve_pair_claims_one_agent_and_one_borrower(
    test_database, reservation_service
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)

    pair = await reservation_service.reserve_pair(campaign.id, "worker-1")

    assert pair is not None
    assert pair.agent.state is AgentState.RESERVED
    assert pair.borrower.status is BorrowerStatus.RESERVED
    assert pair.agent.reserved_by == "worker-1"
    assert pair.borrower.reserved_by == "worker-1"


async def test_concurrent_pair_reservations_never_double_book(
    test_database, reservation_service
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 5)

    results = await asyncio.gather(
        *(reservation_service.reserve_pair(campaign.id, f"worker-{index}") for index in range(20))
    )

    pairs = [pair for pair in results if pair is not None]
    assert len(pairs) <= 5
    assert len({pair.agent.id for pair in pairs}) == len(pairs)
    assert len({pair.borrower.id for pair in pairs}) == len(pairs)

    reserved_agents = await test_database["agents"].count_documents(
        {"state": AgentState.RESERVED.value}
    )
    reserved_borrowers = await test_database["borrowers"].count_documents(
        {"status": BorrowerStatus.RESERVED.value}
    )
    assert reserved_agents == len(pairs)
    assert reserved_borrowers == len(pairs)


async def test_agent_is_released_when_no_borrower_can_be_claimed(
    test_database, reservation_service
):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)

    pair = await reservation_service.reserve_pair(campaign.id, "worker-1")

    assert pair is None
    stored = await test_database["agents"].find_one({"_id": agents[0].id})
    assert stored["state"] == AgentState.AVAILABLE.value
    assert stored["reserved_by"] is None
    assert stored["lease_expires_at"] is None


async def test_reserve_pair_returns_none_when_no_agent_is_available(
    test_database, reservation_service
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.OFFLINE)
    await insert_borrowers(test_database, campaign.id, 5)

    pair = await reservation_service.reserve_pair(campaign.id, "worker-1")

    assert pair is None
    reserved_borrowers = await test_database["borrowers"].count_documents(
        {"status": BorrowerStatus.RESERVED.value}
    )
    assert reserved_borrowers == 0


async def test_release_pair_frees_both_sides(test_database, reservation_service):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)
    pair = await reservation_service.reserve_pair(campaign.id, "worker-1")

    await reservation_service.release_pair(pair, BorrowerReleaseOutcome.RELEASED)

    agent = await test_database["agents"].find_one({"_id": pair.agent.id})
    borrower = await test_database["borrowers"].find_one({"_id": pair.borrower.id})
    assert agent["state"] == AgentState.AVAILABLE.value
    assert agent["reserved_by"] is None
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["reserved_by"] is None
    assert borrower["attempt_count"] == 0


async def test_release_pair_twice_does_not_double_apply_backoff(
    test_database, reservation_service
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)
    pair = await reservation_service.reserve_pair(campaign.id, "worker-1")

    await reservation_service.release_pair(pair, BorrowerReleaseOutcome.RETRY)
    first = await test_database["borrowers"].find_one({"_id": pair.borrower.id})
    await reservation_service.release_pair(pair, BorrowerReleaseOutcome.RETRY)
    second = await test_database["borrowers"].find_one({"_id": pair.borrower.id})

    assert first["attempt_count"] == 1
    assert second["attempt_count"] == 1
    assert second["next_eligible_at"] == first["next_eligible_at"]
    assert second["state_version"] == first["state_version"]


async def test_worker_id_is_stable_within_the_process():
    assert get_worker_id() == get_worker_id()


async def test_built_worker_ids_are_unique_and_structured():
    first = build_worker_id()
    second = build_worker_id()

    assert first != second
    assert len(first.split(":")) == 3
