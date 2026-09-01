import pytest

from app.metrics.registry import (
    COUNTER_RESERVATION_CONTENTION,
    COUNTER_RETRY_ATTEMPTS,
    MetricsRegistry,
)
from app.models.base import utc_now
from app.models.call import Call, build_idempotency_key
from app.models.enums import AgentState, CallState, DialingMode, SafetyVerdict
from app.safety.models import PacingRequest
from tests.conftest import insert_agents, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


async def seed_calls(test_database, campaign, state: CallState, count: int, offset: int = 0):
    documents = []
    for index in range(offset, offset + count):
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
    await test_database["calls"].insert_many(documents)


def make_request(requested: int) -> PacingRequest:
    return PacingRequest(
        requested=requested,
        mode=DialingMode.PREDICTIVE,
        snapshot_captured_at=utc_now(),
        inputs={},
        explanation="test",
    )


async def test_call_state_counts_are_reported(test_database, metrics_collector):
    campaign = await insert_campaign(test_database)
    await seed_calls(test_database, campaign, CallState.RINGING, 3, offset=0)
    await seed_calls(test_database, campaign, CallState.CONNECTED, 2, offset=10)
    await seed_calls(test_database, campaign, CallState.COMPLETED, 4, offset=20)
    await seed_calls(test_database, campaign, CallState.FAILED, 1, offset=30)
    await seed_calls(test_database, campaign, CallState.CANCELLED, 1, offset=40)

    metrics = await metrics_collector.collect(campaign)

    assert metrics.calls_ringing == 3
    assert metrics.calls_connected == 2
    assert metrics.calls_completed == 4
    assert metrics.calls_failed == 1
    assert metrics.calls_cancelled == 1
    assert metrics.active_calls == 5
    assert metrics.calls_initiated == 11


async def test_agent_state_distribution_is_reported(test_database, metrics_collector):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 4, state=AgentState.AVAILABLE)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.CONNECTED)

    metrics = await metrics_collector.collect(campaign)

    assert metrics.agent_states[AgentState.AVAILABLE.value] == 4
    assert metrics.agent_states[AgentState.CONNECTED.value] == 2
    assert metrics.agent_states[AgentState.OFFLINE.value] == 0
    assert set(metrics.agent_states) == {state.value for state in AgentState}


async def test_peak_concurrent_calls_is_remembered_across_samples(
    test_database, metrics_collector
):
    campaign = await insert_campaign(test_database)
    await seed_calls(test_database, campaign, CallState.RINGING, 6)

    first = await metrics_collector.collect(campaign)
    await test_database["calls"].delete_many({})
    second = await metrics_collector.collect(campaign)

    assert first.peak_concurrent_calls == 6
    assert second.active_calls == 0
    assert second.peak_concurrent_calls == 6


async def test_safety_verdict_counts_match_the_persisted_decisions(
    test_database, metrics_collector, safety_controller
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)

    await safety_controller.evaluate(campaign, make_request(100))
    await safety_controller.evaluate(campaign, make_request(2))

    metrics = await metrics_collector.collect(campaign)
    stored = await test_database["safety_decisions"].count_documents({})

    assert stored == 2
    assert sum(metrics.safety_verdicts.values()) == 2
    assert metrics.safety_verdicts[SafetyVerdict.REDUCED.value] == 1
    assert metrics.safety_verdicts[SafetyVerdict.APPROVED.value] == 1
    assert set(metrics.safety_verdicts) == {verdict.value for verdict in SafetyVerdict}


async def test_progressive_fallbacks_are_counted(
    test_database, metrics_collector, safety_controller, health_manager
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    for _ in range(15):
        health_manager.record_originate(campaign.provider_name, success=True, latency_ms=10)
    for _ in range(5):
        health_manager.record_originate(campaign.provider_name, success=False, latency_ms=10)
    health_manager.record_originate(campaign.provider_name, success=True, latency_ms=10)

    await safety_controller.evaluate(campaign, make_request(3))

    metrics = await metrics_collector.collect(campaign)

    assert metrics.progressive_fallbacks == 1


async def test_in_process_counters_are_surfaced(test_database, metrics_collector, metrics_registry):
    campaign = await insert_campaign(test_database)
    metrics_registry.increment(COUNTER_RESERVATION_CONTENTION, 7)
    metrics_registry.increment(COUNTER_RETRY_ATTEMPTS, 3)

    metrics = await metrics_collector.collect(campaign)

    assert metrics.reservation_contention == 7
    assert metrics.retry_attempts == 3
    assert metrics.counters[COUNTER_RESERVATION_CONTENTION] == 7


async def test_answer_rate_is_null_without_completed_calls(test_database, metrics_collector):
    campaign = await insert_campaign(test_database)

    metrics = await metrics_collector.collect(campaign)

    assert metrics.answer_rate is None
    assert metrics.talk_utilization is None


async def test_utilization_is_reported_from_agent_time_accounting(
    test_database, metrics_collector
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await test_database["agents"].update_many(
        {},
        {
            "$set": {
                "connected_time_ms": 60000,
                "busy_time_ms": 60000,
                "available_time_ms": 40000,
            }
        },
    )

    metrics = await metrics_collector.collect(campaign)

    assert metrics.talk_utilization == pytest.approx(0.6)


async def test_sampler_writes_history_for_running_campaigns(
    test_database, metrics_sampler, metrics_repository
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"status": "RUNNING"}}
    )

    sampled = await metrics_sampler.sample_once()
    history = await metrics_repository.find_history(
        campaign.id, since=utc_now().replace(year=2000), limit=10
    )

    assert sampled == 1
    assert len(history) == 1
    assert history[0]["campaign_id"] == campaign.id
    assert history[0]["agent_states"][AgentState.AVAILABLE.value] == 2


async def test_sampler_ignores_campaigns_that_are_not_running(
    test_database, metrics_sampler
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2)

    assert await metrics_sampler.sample_once() == 0


async def test_sampler_failure_never_escapes_the_loop(metrics_sampler, monkeypatch):
    async def explode():
        raise RuntimeError("deliberate metrics failure")

    monkeypatch.setattr(metrics_sampler, "sample_once", explode)
    metrics_sampler.start()
    import asyncio

    await asyncio.sleep(0.05)
    await metrics_sampler.stop()


def test_registry_counters_start_at_zero_and_accumulate():
    registry = MetricsRegistry()

    assert registry.value("anything") == 0
    registry.increment("anything")
    registry.increment("anything", 4)

    assert registry.value("anything") == 5
    assert registry.snapshot() == {"anything": 5}
