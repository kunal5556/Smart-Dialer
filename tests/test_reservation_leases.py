from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, BorrowerStatus
from app.state_machines.agent_sm import TransitionActor
from app.state_machines.errors import InvalidStateTransition, UnauthorizedTransitionActor
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_reservation_collections")

RESERVATION_TTL_SECONDS = 30
SWEEP_LIMIT = 100


async def reserve_single_agent(test_database, agent_repository, worker_id, ttl_seconds):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    reserved = await agent_repository.try_reserve_agent(
        campaign_id=campaign.id,
        agent_id=agents[0].id,
        worker_id=worker_id,
        ttl_seconds=ttl_seconds,
    )
    assert reserved is not None
    return campaign, reserved


async def test_expired_agent_lease_is_reclaimed(test_database, agent_repository):
    _, agent = await reserve_single_agent(test_database, agent_repository, "worker-1", 0)

    reclaimed = await agent_repository.reclaim_expired_agent_leases(
        now=utc_now() + timedelta(seconds=1), limit=SWEEP_LIMIT
    )

    assert [item.id for item in reclaimed] == [agent.id]
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.AVAILABLE.value
    assert stored["reserved_by"] is None
    assert stored["lease_expires_at"] is None
    assert stored["current_call_id"] is None
    assert stored["state_version"] == 2


async def test_unexpired_agent_lease_is_left_alone(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-1", RESERVATION_TTL_SECONDS
    )

    reclaimed = await agent_repository.reclaim_expired_agent_leases(
        now=utc_now(), limit=SWEEP_LIMIT
    )

    assert reclaimed == []
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.RESERVED.value
    assert stored["reserved_by"] == "worker-1"
    assert stored["state_version"] == 1


async def test_reclaim_is_idempotent(test_database, agent_repository):
    _, agent = await reserve_single_agent(test_database, agent_repository, "worker-1", 0)
    now = utc_now() + timedelta(seconds=1)

    first = await agent_repository.reclaim_expired_agent_leases(now=now, limit=SWEEP_LIMIT)
    second = await agent_repository.reclaim_expired_agent_leases(now=now, limit=SWEEP_LIMIT)

    assert len(first) == 1
    assert second == []
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state_version"] == 2


async def test_release_is_guarded_by_lease_ownership(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    released = await agent_repository.release_agent(
        agent_id=agent.id,
        worker_id="worker-b",
        target_state=AgentState.AVAILABLE,
        actor=TransitionActor.ALLOCATOR,
    )

    assert released is None
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.RESERVED.value
    assert stored["reserved_by"] == "worker-a"
    assert stored["state_version"] == 1


async def test_owner_can_release_its_own_reservation(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    released = await agent_repository.release_agent(
        agent_id=agent.id,
        worker_id="worker-a",
        target_state=AgentState.AVAILABLE,
        actor=TransitionActor.ALLOCATOR,
    )

    assert released.state is AgentState.AVAILABLE
    assert released.reserved_by is None
    assert released.state_version == 2


async def test_double_release_is_a_no_op(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    first = await agent_repository.release_agent(
        agent_id=agent.id,
        worker_id="worker-a",
        target_state=AgentState.AVAILABLE,
        actor=TransitionActor.ALLOCATOR,
    )
    second = await agent_repository.release_agent(
        agent_id=agent.id,
        worker_id="worker-a",
        target_state=AgentState.AVAILABLE,
        actor=TransitionActor.ALLOCATOR,
    )

    assert first is not None
    assert second is None
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state_version"] == 2


async def test_release_rejects_an_actor_that_may_not_perform_it(
    test_database, agent_repository
):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    released = await agent_repository.release_agent(
        agent_id=agent.id,
        worker_id="worker-a",
        target_state=AgentState.DIALING,
        actor=TransitionActor.EVENT_PROCESSOR,
    )

    assert released is None
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.RESERVED.value


async def test_transition_agent_applies_a_legal_transition(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    updated = await agent_repository.transition_agent(
        agent_id=agent.id,
        from_state=AgentState.RESERVED,
        to_state=AgentState.DIALING,
        actor=TransitionActor.ALLOCATOR,
        expected_version=agent.state_version,
    )

    assert updated.state is AgentState.DIALING
    assert updated.state_version == agent.state_version + 1
    assert updated.reserved_by == "worker-a"


async def test_transition_agent_rejects_an_illegal_transition(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    with pytest.raises(InvalidStateTransition):
        await agent_repository.transition_agent(
            agent_id=agent.id,
            from_state=AgentState.RESERVED,
            to_state=AgentState.CONNECTED,
            actor=TransitionActor.ALLOCATOR,
            expected_version=agent.state_version,
        )


async def test_transition_agent_rejects_an_unauthorized_actor(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    with pytest.raises(UnauthorizedTransitionActor):
        await agent_repository.transition_agent(
            agent_id=agent.id,
            from_state=AgentState.RESERVED,
            to_state=AgentState.DIALING,
            actor=TransitionActor.EVENT_PROCESSOR,
            expected_version=agent.state_version,
        )


async def test_transition_agent_rejects_a_stale_version(test_database, agent_repository):
    _, agent = await reserve_single_agent(
        test_database, agent_repository, "worker-a", RESERVATION_TTL_SECONDS
    )

    updated = await agent_repository.transition_agent(
        agent_id=agent.id,
        from_state=AgentState.RESERVED,
        to_state=AgentState.DIALING,
        actor=TransitionActor.ALLOCATOR,
        expected_version=agent.state_version + 5,
    )

    assert updated is None
    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.RESERVED.value


async def test_expired_borrower_lease_is_reclaimed(test_database, borrower_repository):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 1)
    reserved = await borrower_repository.try_reserve_borrower(
        campaign_id=campaign.id,
        borrower_id=borrowers[0].id,
        worker_id="worker-1",
        ttl_seconds=0,
    )
    assert reserved is not None

    reclaimed = await borrower_repository.reclaim_expired_borrower_leases(
        now=utc_now() + timedelta(seconds=1), limit=SWEEP_LIMIT
    )

    assert [item.id for item in reclaimed] == [borrowers[0].id]
    stored = await test_database["borrowers"].find_one({"_id": borrowers[0].id})
    assert stored["status"] == BorrowerStatus.PENDING.value
    assert stored["reserved_by"] is None
    assert stored["attempt_count"] == 0


async def test_unexpired_borrower_lease_is_left_alone(test_database, borrower_repository):
    campaign = await insert_campaign(test_database)
    borrowers = await insert_borrowers(test_database, campaign.id, 1)
    await borrower_repository.try_reserve_borrower(
        campaign_id=campaign.id,
        borrower_id=borrowers[0].id,
        worker_id="worker-1",
        ttl_seconds=RESERVATION_TTL_SECONDS,
    )

    reclaimed = await borrower_repository.reclaim_expired_borrower_leases(
        now=utc_now(), limit=SWEEP_LIMIT
    )

    assert reclaimed == []
    stored = await test_database["borrowers"].find_one({"_id": borrowers[0].id})
    assert stored["status"] == BorrowerStatus.RESERVED.value
