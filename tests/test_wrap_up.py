from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState
from app.services.wrap_up_service import WrapUpService
from tests.conftest import insert_agents, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


@pytest.fixture
def wrap_up_service(agent_repository, test_settings) -> WrapUpService:
    return WrapUpService(agent_repository, test_settings)


async def set_wrap_up_since(test_database, agent_id: str, seconds_ago: float) -> None:
    await test_database["agents"].update_one(
        {"_id": agent_id},
        {
            "$set": {
                "state": AgentState.WRAP_UP.value,
                "state_changed_at": utc_now() - timedelta(seconds=seconds_ago),
            }
        },
    )


async def test_agent_still_within_the_wrap_up_window_is_not_returned(
    test_database, wrap_up_service, test_settings
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)
    await set_wrap_up_since(test_database, agent.id, test_settings.WRAP_UP_SECONDS / 2)

    expired = await wrap_up_service.find_finished_wrap_ups(campaign.id)

    assert expired == []


async def test_agent_past_the_wrap_up_window_is_returned(
    test_database, wrap_up_service, test_settings
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)
    await set_wrap_up_since(test_database, agent.id, test_settings.WRAP_UP_SECONDS + 5)

    expired = await wrap_up_service.find_finished_wrap_ups(campaign.id)

    assert [item.id for item in expired] == [agent.id]
    assert expired[0].state is AgentState.WRAP_UP


async def test_agents_in_other_states_are_never_returned(test_database, wrap_up_service):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.CONNECTED)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)

    expired = await wrap_up_service.find_finished_wrap_ups(campaign.id)

    assert expired == []


async def test_only_the_requested_campaign_is_swept(
    test_database, wrap_up_service, test_settings
):
    campaign = await insert_campaign(test_database, name="Campaign A")
    other = await insert_campaign(test_database, name="Campaign B")
    [mine] = await insert_agents(test_database, campaign.id, 1)
    [theirs] = await insert_agents(test_database, other.id, 1)
    await set_wrap_up_since(test_database, mine.id, test_settings.WRAP_UP_SECONDS + 5)
    await set_wrap_up_since(test_database, theirs.id, test_settings.WRAP_UP_SECONDS + 5)

    expired = await wrap_up_service.find_finished_wrap_ups(campaign.id)

    assert [item.id for item in expired] == [mine.id]


async def test_sweep_honours_the_limit(test_database, wrap_up_service, test_settings):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 5)
    for agent in agents:
        await set_wrap_up_since(test_database, agent.id, test_settings.WRAP_UP_SECONDS + 5)

    expired = await wrap_up_service.find_finished_wrap_ups(campaign.id, limit=2)

    assert len(expired) == 2
