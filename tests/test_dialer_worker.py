import asyncio
from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, CampaignStatus
from app.workers.dialer_worker import DialerWorker
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


@pytest.fixture
def dialer_worker(
    campaign_repository, agent_repository, mode_router, wrap_up_service, test_settings
) -> DialerWorker:
    test_settings.DIALER_TICK_SECONDS = 0.01
    return DialerWorker(
        campaign_repository=campaign_repository,
        agent_repository=agent_repository,
        mode_router=mode_router,
        wrap_up_service=wrap_up_service,
        settings=test_settings,
    )


async def start_campaign(test_database, campaign_id: str) -> None:
    await test_database["campaigns"].update_one(
        {"_id": campaign_id}, {"$set": {"status": CampaignStatus.RUNNING.value}}
    )


async def test_worker_only_dials_running_campaigns(test_database, dialer_worker):
    draft = await insert_campaign(test_database, name="Draft Campaign")
    await insert_agents(test_database, draft.id, 3, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, draft.id, 10)

    await dialer_worker.run_once()

    assert await test_database["calls"].count_documents({}) == 0


async def test_worker_dials_a_running_campaign(test_database, dialer_worker):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 10)
    await start_campaign(test_database, campaign.id)

    await dialer_worker.run_once()

    assert await test_database["calls"].count_documents({}) == 3


async def test_worker_releases_agents_whose_wrap_up_expired(
    test_database, dialer_worker, test_settings
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)
    await start_campaign(test_database, campaign.id)
    await test_database["agents"].update_one(
        {"_id": agent.id},
        {
            "$set": {
                "state": AgentState.WRAP_UP.value,
                "state_changed_at": utc_now()
                - timedelta(seconds=test_settings.WRAP_UP_SECONDS + 5),
            }
        },
    )

    await dialer_worker.run_once()

    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.AVAILABLE.value


async def test_worker_leaves_agents_still_in_wrap_up_alone(
    test_database, dialer_worker, test_settings
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)
    await start_campaign(test_database, campaign.id)
    await test_database["agents"].update_one(
        {"_id": agent.id},
        {"$set": {"state": AgentState.WRAP_UP.value, "state_changed_at": utc_now()}},
    )

    await dialer_worker.run_once()

    stored = await test_database["agents"].find_one({"_id": agent.id})
    assert stored["state"] == AgentState.WRAP_UP.value


async def test_a_failing_campaign_tick_does_not_stop_other_campaigns(
    test_database, dialer_worker, mode_router
):
    broken = await insert_campaign(test_database, name="Broken Campaign")
    healthy = await insert_campaign(test_database, name="Healthy Campaign")
    await insert_agents(test_database, healthy.id, 2, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, healthy.id, 5)
    await start_campaign(test_database, broken.id)
    await start_campaign(test_database, healthy.id)

    original_select = mode_router.select

    def select(campaign):
        if campaign.id == broken.id:
            raise RuntimeError("deliberate tick failure")
        return original_select(campaign)

    mode_router.select = select

    await dialer_worker.run_once()

    assert await test_database["calls"].count_documents({"campaign_id": healthy.id}) == 2


async def test_worker_loop_survives_an_exception_and_keeps_ticking(
    test_database, dialer_worker, campaign_repository
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 10)
    await start_campaign(test_database, campaign.id)

    calls_seen = {"count": 0}
    original_find_running = campaign_repository.find_running

    async def flaky_find_running(limit: int = 100):
        calls_seen["count"] += 1
        if calls_seen["count"] == 1:
            raise RuntimeError("deliberate database blip")
        return await original_find_running(limit)

    campaign_repository.find_running = flaky_find_running

    dialer_worker.start()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if calls_seen["count"] >= 3:
            break
    await dialer_worker.stop()

    assert calls_seen["count"] >= 3
    assert await test_database["calls"].count_documents({}) == 2


async def test_stopping_a_worker_that_never_started_is_safe(dialer_worker):
    await dialer_worker.stop()


async def test_start_is_idempotent(dialer_worker):
    dialer_worker.start()
    dialer_worker.start()
    await dialer_worker.stop()
