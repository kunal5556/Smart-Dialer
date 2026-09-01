import asyncio

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, ProviderHealthStatus, SafetyVerdict
from app.safety.models import SafetyDecision
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

PROVIDER = "mock_b"


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


def test_retry_is_allowed_while_healthy(health_manager):
    assert health_manager.should_allow_retry(PROVIDER) is True


def test_retry_is_blocked_while_unhealthy(health_manager):
    for _ in range(10):
        health_manager.record_originate(PROVIDER, success=False, latency_ms=50)

    assert health_manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY
    assert health_manager.should_allow_retry(PROVIDER) is False


def test_retry_reopens_after_recovery(health_manager):
    for _ in range(10):
        health_manager.record_originate(PROVIDER, success=False, latency_ms=50)
    assert health_manager.should_allow_retry(PROVIDER) is False

    for _ in range(60):
        health_manager.record_originate(PROVIDER, success=True, latency_ms=50)

    assert health_manager.should_allow_retry(PROVIDER) is True


def test_degraded_provider_still_allows_retries(health_manager):
    for _ in range(15):
        health_manager.record_originate(PROVIDER, success=True, latency_ms=50)
    for _ in range(5):
        health_manager.record_originate(PROVIDER, success=False, latency_ms=50)
    health_manager.record_originate(PROVIDER, success=True, latency_ms=50)

    assert health_manager.get_health(PROVIDER).status is ProviderHealthStatus.DEGRADED
    assert health_manager.should_allow_retry(PROVIDER) is True


async def test_provider_outage_drives_health_to_unhealthy_through_real_traffic(
    test_database, call_allocator, fast_provider_registry, health_manager, test_settings
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"provider_name": PROVIDER})
    await insert_agents(test_database, campaign.id, 8, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 20)

    provider = fast_provider_registry.get(PROVIDER)
    test_settings.PROVIDER_TIMEOUT_SECONDS = 0.01
    provider.force_outage(60)

    await call_allocator.allocate(campaign, approve(campaign.id, 8), "worker-1")

    health = health_manager.get_health(PROVIDER)
    assert health.status is ProviderHealthStatus.UNHEALTHY
    assert health.consecutive_failures >= test_settings.UNHEALTHY_CONSECUTIVE_FAILURES
    assert health_manager.should_allow_retry(PROVIDER) is False

    calls = await test_database["calls"].find({}).to_list(None)
    assert calls
    assert all(call["state"] == "FAILED" for call in calls)
    assert all(call["failure_reason"] == "provider_timeout" for call in calls)

    agents = await test_database["agents"].find({}).to_list(None)
    assert all(agent["state"] == AgentState.AVAILABLE.value for agent in agents)
    assert all(agent["reserved_by"] is None for agent in agents)


async def test_health_snapshots_are_persisted_for_history(
    test_database, health_manager, health_repository
):
    for _ in range(3):
        health_manager.record_originate(PROVIDER, success=True, latency_ms=120)

    await health_repository.record_snapshot(health_manager.get_health(PROVIDER))
    await asyncio.sleep(0)
    await health_repository.record_snapshot(health_manager.get_health(PROVIDER))

    stored = await health_repository.find_recent(
        PROVIDER, since=utc_now().replace(year=2000), limit=10
    )

    assert len(stored) == 2
    assert stored[0]["provider_name"] == PROVIDER
    assert stored[0]["status"] == ProviderHealthStatus.HEALTHY.value
    assert stored[0]["request_count"] == 3
