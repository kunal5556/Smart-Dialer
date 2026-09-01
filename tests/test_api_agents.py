import pytest

from app.models.enums import AgentState
from tests.conftest import insert_agents, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


async def test_agent_list_includes_a_state_summary(test_database, api_client):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.OFFLINE)

    body = api_client.get(f"/api/campaigns/{campaign.id}/agents").json()

    assert body["campaign_id"] == campaign.id
    assert body["state_summary"][AgentState.AVAILABLE.value] == 3
    assert body["state_summary"][AgentState.OFFLINE.value] == 2
    assert len(body["agents"]) == 5
    assert set(body["state_summary"]) == {state.value for state in AgentState}


async def test_login_moves_the_agent_online_and_records_a_heartbeat(
    test_database, api_client
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)

    body = api_client.post(f"/api/agents/{agent.id}/login").json()

    assert body["state"] == AgentState.AVAILABLE.value
    assert body["last_heartbeat_at"] is not None


async def test_pause_and_resume_round_trip(test_database, api_client):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)

    paused = api_client.post(f"/api/agents/{agent.id}/pause").json()
    resumed = api_client.post(f"/api/agents/{agent.id}/resume").json()

    assert paused["state"] == AgentState.PAUSED.value
    assert resumed["state"] == AgentState.AVAILABLE.value


async def test_logout_takes_the_agent_offline(test_database, api_client):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)

    body = api_client.post(f"/api/agents/{agent.id}/logout").json()

    assert body["state"] == AgentState.OFFLINE.value


async def test_heartbeat_updates_the_timestamp_without_changing_state(
    test_database, api_client
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)

    body = api_client.post(f"/api/agents/{agent.id}/heartbeat").json()

    assert body["state"] == AgentState.AVAILABLE.value
    assert body["last_heartbeat_at"] is not None


async def test_an_illegal_agent_action_returns_conflict(test_database, api_client):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.OFFLINE)

    response = api_client.post(f"/api/agents/{agent.id}/pause")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"]["from"] == AgentState.OFFLINE.value


def test_unknown_agent_returns_not_found(api_client):
    response = api_client.post("/api/agents/missing-agent/login")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_unknown_agent_action_returns_not_found(test_database, api_client):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1)

    response = api_client.post(f"/api/agents/{agent.id}/teleport")

    assert response.status_code == 404
