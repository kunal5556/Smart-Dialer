import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, BorrowerStatus, CallState, SafetyVerdict
from app.safety.models import SafetyDecision
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


def approve(campaign_id: str, approved: int) -> SafetyDecision:
    return SafetyDecision(
        campaign_id=campaign_id,
        requested=approved,
        approved=approved,
        verdict=SafetyVerdict.APPROVED,
        constraints=[],
        binding_constraint=None,
        snapshot_age_ms=0,
        created_at=utc_now(),
    )


async def test_allocator_creates_one_call_per_approved_slot(
    test_database, call_allocator
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 10)

    result = await call_allocator.allocate(campaign, approve(campaign.id, 3), "worker-1")

    assert result.attempted == 3
    assert result.allocated == 3
    assert result.failed == 0
    assert await test_database["calls"].count_documents({}) == 3
    assert (
        await test_database["calls"].count_documents({"state": CallState.INITIATED.value}) == 3
    )


async def test_allocator_never_exceeds_available_agents(test_database, call_allocator):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 50)

    result = await call_allocator.allocate(campaign, approve(campaign.id, 20), "worker-1")

    assert result.allocated == 2
    assert await test_database["calls"].count_documents({}) == 2


async def test_allocator_stops_early_on_repeated_contention(test_database, call_allocator):
    campaign = await insert_campaign(test_database)
    await insert_borrowers(test_database, campaign.id, 10)

    result = await call_allocator.allocate(campaign, approve(campaign.id, 20), "worker-1")

    assert result.allocated == 0
    assert result.attempted < 20
    assert result.contended == result.attempted


async def test_zero_approved_slots_dials_nothing(test_database, call_allocator):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 5)

    result = await call_allocator.allocate(campaign, approve(campaign.id, 0), "worker-1")

    assert result == type(result)(attempted=0, allocated=0, failed=0, contended=0)
    assert await test_database["calls"].count_documents({}) == 0
    assert (
        await test_database["agents"].count_documents({"state": AgentState.AVAILABLE.value}) == 5
    )


async def test_agent_and_borrower_are_bound_to_the_call(test_database, call_allocator):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)

    await call_allocator.allocate(campaign, approve(campaign.id, 1), "worker-1")

    call = await test_database["calls"].find_one({})
    agent = await test_database["agents"].find_one({"_id": call["agent_id"]})
    borrower = await test_database["borrowers"].find_one({"_id": call["borrower_id"]})

    assert agent["state"] == AgentState.DIALING.value
    assert borrower["status"] == BorrowerStatus.RESERVED.value
    assert call["provider_call_id"] is not None
    assert call["created_by_worker"] == "worker-1"


async def test_originate_rejection_fails_the_call_and_releases_both(
    test_database, call_allocator, fast_provider_registry
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)
    fast_provider_registry.get(campaign.provider_name).behaviour.failure_rate = 1.0

    result = await call_allocator.allocate(campaign, approve(campaign.id, 1), "worker-1")

    assert result.allocated == 0
    assert result.failed == 1

    call = await test_database["calls"].find_one({})
    agent = await test_database["agents"].find_one({"_id": call["agent_id"]})
    borrower = await test_database["borrowers"].find_one({"_id": call["borrower_id"]})

    assert call["state"] == CallState.FAILED.value
    assert call["failure_reason"] == "carrier_rejected"
    assert agent["state"] == AgentState.AVAILABLE.value
    assert agent["reserved_by"] is None
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["reserved_by"] is None
    assert borrower["attempt_count"] == 1


async def test_originate_timeout_fails_the_call_and_releases_both(
    test_database, call_allocator, fast_provider_registry, test_settings
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)
    provider = fast_provider_registry.get(campaign.provider_name)
    provider.behaviour.hang_rate = 1.0
    test_settings.PROVIDER_TIMEOUT_SECONDS = 0.02

    result = await call_allocator.allocate(campaign, approve(campaign.id, 1), "worker-1")

    assert result.failed == 1
    call = await test_database["calls"].find_one({})
    agent = await test_database["agents"].find_one({"_id": call["agent_id"]})

    assert call["state"] == CallState.FAILED.value
    assert call["failure_reason"] == "provider_timeout"
    assert agent["state"] == AgentState.AVAILABLE.value
    assert agent["reserved_by"] is None


async def test_timeout_is_recorded_against_provider_health(
    test_database, call_allocator, fast_provider_registry, health_manager, test_settings
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)
    fast_provider_registry.get(campaign.provider_name).behaviour.hang_rate = 1.0
    test_settings.PROVIDER_TIMEOUT_SECONDS = 0.02

    await call_allocator.allocate(campaign, approve(campaign.id, 1), "worker-1")

    health = health_manager.get_health(campaign.provider_name)
    assert health.request_count == 1
    assert health.timeout_rate == 1.0


async def test_successful_originate_is_recorded_against_provider_health(
    test_database, call_allocator, health_manager
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 2)

    await call_allocator.allocate(campaign, approve(campaign.id, 2), "worker-1")

    health = health_manager.get_health(campaign.provider_name)
    assert health.request_count == 2
    assert health.success_rate == 1.0


async def test_duplicate_call_is_not_dialed_twice(
    test_database, call_allocator, call_repository
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    [borrower] = await insert_borrowers(test_database, campaign.id, 1)
    existing = await call_repository.create_call(
        campaign_id=campaign.id,
        agent_id=agent.id,
        borrower_id=borrower.id,
        provider_name=campaign.provider_name,
        worker_id="worker-0",
        attempt=1,
    )
    await call_repository.transition_call(existing.id, CallState.RESERVED)

    result = await call_allocator.allocate(campaign, approve(campaign.id, 1), "worker-1")

    assert result.allocated == 0
    assert await test_database["calls"].count_documents({}) == 1
    agent_document = await test_database["agents"].find_one({"_id": agent.id})
    assert agent_document["state"] == AgentState.AVAILABLE.value


async def test_partial_allocation_leaves_nothing_reserved(test_database, call_allocator):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 2)

    result = await call_allocator.allocate(campaign, approve(campaign.id, 5), "worker-1")

    assert result.allocated == 2
    reserved_agents = await test_database["agents"].count_documents(
        {"state": AgentState.RESERVED.value}
    )
    assert reserved_agents == 0
    assert (
        await test_database["agents"].count_documents({"state": AgentState.AVAILABLE.value}) == 3
    )
