from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.campaign import Campaign
from app.models.enums import AgentState, CallState, DialingMode, SafetyVerdict
from app.safety.models import PacingRequest
from app.safety.safety_controller import (
    CONSTRAINT_AGENT_CAPACITY,
    CONSTRAINT_CAMPAIGN_CONCURRENCY,
    CONSTRAINT_PROVIDER_HEALTH,
    CONSTRAINT_RINGING_CEILING,
    CONSTRAINT_STALE_STATE,
)
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


def make_request(
    requested: int,
    mode: DialingMode = DialingMode.PREDICTIVE,
    inputs: dict | None = None,
    captured_at=None,
) -> PacingRequest:
    return PacingRequest(
        requested=requested,
        mode=mode,
        snapshot_captured_at=captured_at or utc_now(),
        inputs=inputs or {},
        explanation="test request",
    )


async def seed_calls(test_database, campaign: Campaign, state: CallState, count: int) -> None:
    from app.models.call import Call, build_idempotency_key

    documents = []
    for index in range(count):
        call = Call(
            campaign_id=campaign.id,
            agent_id=f"agent-{index}",
            borrower_id=f"borrower-{index}",
            provider_name=campaign.provider_name,
            created_by_worker="worker-1",
            state=state,
            idempotency_key=build_idempotency_key(
                campaign.id, f"agent-{index}", f"borrower-{index}", 1
            ),
        )
        documents.append(call.to_mongo())
    if documents:
        await test_database["calls"].insert_many(documents)


async def test_over_request_is_reduced_to_agent_capacity(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 8, state=AgentState.AVAILABLE)

    decision = await safety_controller.evaluate(campaign, make_request(100))

    assert decision.approved == 8
    assert decision.verdict is SafetyVerdict.REDUCED
    assert decision.binding_constraint == CONSTRAINT_AGENT_CAPACITY


async def test_no_agents_means_rejection(test_database, safety_controller):
    campaign = await insert_campaign(test_database)

    decision = await safety_controller.evaluate(campaign, make_request(10))

    assert decision.approved == 0
    assert decision.verdict is SafetyVerdict.REJECTED


async def test_request_within_capacity_is_approved(test_database, safety_controller):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 20, state=AgentState.AVAILABLE)

    decision = await safety_controller.evaluate(campaign, make_request(5))

    assert decision.approved == 5
    assert decision.verdict is SafetyVerdict.APPROVED
    assert decision.binding_constraint is None


async def test_campaign_concurrency_cap_can_bind(test_database, safety_controller):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 2})
    await insert_agents(test_database, campaign.id, 20, state=AgentState.AVAILABLE)

    decision = await safety_controller.evaluate(campaign, make_request(10))

    assert decision.approved == 2
    assert decision.binding_constraint == CONSTRAINT_CAMPAIGN_CONCURRENCY


async def test_unhealthy_provider_rejects_regardless_of_agents(
    test_database, safety_controller, health_manager
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 50, state=AgentState.AVAILABLE)
    for _ in range(10):
        health_manager.record_originate(campaign.provider_name, success=False, latency_ms=10)

    decision = await safety_controller.evaluate(campaign, make_request(20))

    assert decision.approved == 0
    assert decision.verdict is SafetyVerdict.REJECTED
    assert decision.binding_constraint == CONSTRAINT_PROVIDER_HEALTH


async def test_degraded_provider_caps_at_progressive_equivalent(
    test_database, safety_controller, health_manager
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    await insert_agents(test_database, campaign.id, 6, state=AgentState.AVAILABLE)
    for _ in range(15):
        health_manager.record_originate(campaign.provider_name, success=True, latency_ms=10)
    for _ in range(5):
        health_manager.record_originate(campaign.provider_name, success=False, latency_ms=10)
    health_manager.record_originate(campaign.provider_name, success=True, latency_ms=10)

    decision = await safety_controller.evaluate(campaign, make_request(100))

    assert decision.approved == 6
    assert decision.verdict is SafetyVerdict.FALLBACK_PROGRESSIVE
    assert decision.fallback_reason == "provider_degraded"


async def test_ringing_ceiling_binds_when_ringing_is_excessive(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    await insert_agents(test_database, campaign.id, 4, state=AgentState.AVAILABLE)
    await seed_calls(test_database, campaign, CallState.RINGING, 7)

    decision = await safety_controller.evaluate(campaign, make_request(50))

    assert decision.approved == 1
    assert decision.binding_constraint == CONSTRAINT_RINGING_CEILING


async def test_stale_snapshot_rejects_everything(test_database, safety_controller, test_settings):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 20, state=AgentState.AVAILABLE)
    stale = utc_now() - timedelta(seconds=test_settings.MAX_SNAPSHOT_AGE_SECONDS + 5)

    decision = await safety_controller.evaluate(campaign, make_request(10, captured_at=stale))

    assert decision.approved == 0
    assert decision.verdict is SafetyVerdict.REJECTED
    assert decision.binding_constraint == CONSTRAINT_STALE_STATE
    assert decision.snapshot_age_ms > test_settings.MAX_SNAPSHOT_AGE_SECONDS * 1000


async def test_unresolved_expired_leases_reject_everything(
    test_database, safety_controller, agent_repository
):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await agent_repository.try_reserve_agent(
        campaign_id=campaign.id,
        agent_id=agents[0].id,
        worker_id="dead-worker",
        ttl_seconds=30,
    )
    await test_database["agents"].update_one(
        {"_id": agents[0].id},
        {"$set": {"lease_expires_at": utc_now() - timedelta(seconds=60)}},
    )

    decision = await safety_controller.evaluate(campaign, make_request(4))

    assert decision.approved == 0
    assert decision.binding_constraint == CONSTRAINT_STALE_STATE


async def test_availability_drop_forces_progressive_fallback(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    agents = await insert_agents(test_database, campaign.id, 100, state=AgentState.AVAILABLE)
    await safety_controller.evaluate(campaign, make_request(1))

    disappearing = [agent.id for agent in agents[:40]]
    await test_database["agents"].update_many(
        {"_id": {"$in": disappearing}}, {"$set": {"state": AgentState.OFFLINE.value}}
    )

    decision = await safety_controller.evaluate(campaign, make_request(100))

    assert decision.verdict is SafetyVerdict.FALLBACK_PROGRESSIVE
    assert decision.fallback_reason == "availability_drop"
    assert decision.approved == 60


async def test_progressive_mode_is_capped_at_available_agents(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 7, state=AgentState.AVAILABLE)

    decision = await safety_controller.evaluate(
        campaign, make_request(100, mode=DialingMode.PROGRESSIVE)
    )

    assert decision.approved == 7


async def test_controller_ignores_inflated_request_inputs(test_database, safety_controller):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)

    decision = await safety_controller.evaluate(
        campaign,
        make_request(500, inputs={"available_agents": 500, "free_capacity": 500}),
    )

    assert decision.approved == 3
    assert decision.binding_constraint == CONSTRAINT_AGENT_CAPACITY


async def test_evaluation_is_deterministic(test_database, safety_controller):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 9, state=AgentState.AVAILABLE)
    request = make_request(20)

    first = await safety_controller.evaluate(campaign, request)
    second = await safety_controller.evaluate(campaign, request)

    assert first.approved == second.approved
    assert first.verdict is second.verdict
    assert first.binding_constraint == second.binding_constraint
    assert [item.limit for item in first.constraints] == [
        item.limit for item in second.constraints
    ]


async def test_evaluation_errors_fail_closed(test_database, safety_controller, monkeypatch):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 20, state=AgentState.AVAILABLE)

    async def explode(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(safety_controller, "_read_capacity", explode)

    decision = await safety_controller.evaluate(campaign, make_request(10))

    assert decision.approved == 0
    assert decision.verdict is SafetyVerdict.REJECTED
    assert decision.binding_constraint == "evaluation_error"


async def test_every_decision_is_persisted_with_its_constraints(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 4, state=AgentState.AVAILABLE)

    await safety_controller.evaluate(campaign, make_request(10))

    stored = await test_database["safety_decisions"].find_one({})
    assert stored["requested"] == 10
    assert stored["approved"] == 4
    assert stored["binding_constraint"] == CONSTRAINT_AGENT_CAPACITY
    assert len(stored["constraints"]) == 8
    assert any(constraint["binding"] for constraint in stored["constraints"])


async def test_approved_is_never_negative(test_database, safety_controller):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 1})
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await seed_calls(test_database, campaign, CallState.CONNECTED, 5)

    decision = await safety_controller.evaluate(campaign, make_request(10))

    assert decision.approved == 0
    assert all(constraint.limit >= 0 for constraint in decision.constraints)


async def test_high_failure_rate_caps_at_progressive_equivalent(
    test_database, safety_controller, call_repository
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 10)

    for index in range(10):
        call = await call_repository.create_call(
            campaign_id=campaign.id,
            agent_id=f"agent-{index}",
            borrower_id=f"borrower-{index}",
            provider_name=campaign.provider_name,
            worker_id="worker-1",
        )
        await call_repository.transition_call(call.id, CallState.FAILED)

    decision = await safety_controller.evaluate(campaign, make_request(100))

    assert decision.approved == 5
    assert decision.fallback_reason == "high_failure_rate"
    assert decision.verdict is SafetyVerdict.FALLBACK_PROGRESSIVE


async def test_a_tick_that_asks_for_nothing_is_not_a_rejection(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)

    decision = await safety_controller.evaluate(campaign, make_request(0))

    assert decision.requested == 0
    assert decision.approved == 0
    assert decision.verdict is SafetyVerdict.APPROVED
    assert decision.binding_constraint is None


async def test_a_real_request_blocked_to_zero_is_still_a_rejection(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)

    decision = await safety_controller.evaluate(campaign, make_request(10))

    assert decision.verdict is SafetyVerdict.REJECTED
