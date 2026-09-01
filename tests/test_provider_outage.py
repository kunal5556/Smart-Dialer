import asyncio

import pytest

from app.models.base import utc_now
from app.models.enums import (
    AgentState,
    BorrowerStatus,
    CallState,
    DialingMode,
    ProviderHealthStatus,
    SafetyVerdict,
)
from app.safety.models import PacingRequest
from app.safety.safety_controller import CONSTRAINT_PROVIDER_HEALTH
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

PROVIDER = "mock_b"

NON_TERMINAL = [
    CallState.QUEUED.value,
    CallState.RESERVED.value,
    CallState.INITIATED.value,
    CallState.RINGING.value,
    CallState.ANSWERED.value,
    CallState.CONNECTED.value,
]


def make_request(requested: int) -> PacingRequest:
    return PacingRequest(
        requested=requested,
        mode=DialingMode.PREDICTIVE,
        snapshot_captured_at=utc_now(),
        inputs={},
        explanation="test",
    )


async def outage_campaign(test_database, agents: int = 10, borrowers: int = 60):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(
        update={"provider_name": PROVIDER, "max_concurrent_calls": 500}
    )
    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"provider_name": PROVIDER}}
    )
    await insert_agents(test_database, campaign.id, agents, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, borrowers)
    return campaign


async def test_outage_drives_health_unhealthy_and_safety_to_zero(
    test_database, mode_router, fast_provider_registry, health_manager,
    safety_controller, test_settings
):
    campaign = await outage_campaign(test_database)
    provider = fast_provider_registry.get(PROVIDER)
    test_settings.PROVIDER_TIMEOUT_SECONDS = 0.01

    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"dialing_mode": DialingMode.PREDICTIVE.value}}
    )
    campaign = campaign.model_copy(update={"dialing_mode": DialingMode.PREDICTIVE})

    provider.force_outage(60)
    for _ in range(3):
        await mode_router.select(campaign).tick(campaign, "worker-1")

    attempted = await test_database["calls"].count_documents({})
    assert attempted > 0
    assert health_manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY

    decision = await safety_controller.evaluate(campaign, make_request(50))
    assert decision.approved == 0
    assert decision.verdict is SafetyVerdict.REJECTED
    assert decision.binding_constraint == CONSTRAINT_PROVIDER_HEALTH


async def test_no_new_calls_are_created_during_an_outage(
    test_database, mode_router, fast_provider_registry, test_settings
):
    campaign = await outage_campaign(test_database)
    provider = fast_provider_registry.get(PROVIDER)
    test_settings.PROVIDER_TIMEOUT_SECONDS = 0.01
    provider.force_outage(60)

    for _ in range(3):
        await mode_router.select(campaign).tick(campaign, "worker-1")

    after_outage_start = await test_database["calls"].count_documents({})
    for _ in range(3):
        await mode_router.select(campaign).tick(campaign, "worker-1")

    assert await test_database["calls"].count_documents({}) == after_outage_start
    assert await test_database["calls"].count_documents(
        {"state": {"$in": NON_TERMINAL}}
    ) == 0


async def test_existing_calls_are_never_mass_cancelled_by_an_outage(
    test_database, mode_router, fast_provider_registry, recovery_worker, test_settings
):
    campaign = await outage_campaign(test_database, agents=4, borrowers=20)
    provider = fast_provider_registry.get(PROVIDER)

    await mode_router.select(campaign).tick(campaign, "worker-1")
    live_calls = await test_database["calls"].count_documents(
        {"state": {"$in": NON_TERMINAL}}
    )
    assert live_calls > 0

    test_settings.PROVIDER_TIMEOUT_SECONDS = 0.01
    provider.force_outage(60)
    await recovery_worker.run_sweeps()

    still_live = await test_database["calls"].count_documents(
        {"state": {"$in": NON_TERMINAL}}
    )
    assert still_live == live_calls


async def test_dialing_resumes_after_the_outage_clears(
    test_database, mode_router, fast_provider_registry, health_manager, test_settings
):
    campaign = await outage_campaign(test_database)
    provider = fast_provider_registry.get(PROVIDER)
    test_settings.PROVIDER_TIMEOUT_SECONDS = 0.01
    provider.force_outage(60)

    for _ in range(3):
        await mode_router.select(campaign).tick(campaign, "worker-1")
    assert health_manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY

    provider.clear_outage()
    for _ in range(30):
        health_manager.record_originate(PROVIDER, success=True, latency_ms=10)
    assert health_manager.get_health(PROVIDER).status is not ProviderHealthStatus.UNHEALTHY

    await test_database["borrowers"].update_many({}, {"$set": {"next_eligible_at": utc_now()}})
    before = await test_database["calls"].count_documents({})
    await mode_router.select(campaign).tick(campaign, "worker-1")

    assert await test_database["calls"].count_documents({}) > before


async def test_pacing_drives_the_request_toward_zero_before_safety_rejects(
    test_database, mode_router, health_manager
):
    campaign = await outage_campaign(test_database)
    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"dialing_mode": DialingMode.PREDICTIVE.value}}
    )
    campaign = campaign.model_copy(update={"dialing_mode": DialingMode.PREDICTIVE})

    for _ in range(10):
        health_manager.record_originate(PROVIDER, success=False, latency_ms=10)

    result = await mode_router.select(campaign).tick(campaign, "worker-1")

    assert result.requested == 0
    assert result.decision.approved == 0
