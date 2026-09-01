import pytest

from app.models.enums import AgentState, CallState, DialingMode
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


def create_campaign(client, name="API Campaign", **overrides) -> dict:
    payload = {"name": name}
    payload.update(overrides)
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_openapi_lists_every_router(api_client):
    schema = api_client.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/api/campaigns" in paths
    assert "/api/campaigns/{campaign_id}/agents" in paths
    assert "/api/campaigns/{campaign_id}/calls" in paths
    assert "/api/campaigns/{campaign_id}/pacing-decisions" in paths
    assert "/api/campaigns/{campaign_id}/safety-decisions" in paths
    assert "/api/campaigns/{campaign_id}/metrics" in paths
    assert "/api/providers/health" in paths
    assert "/api/simulations" in paths


def test_campaign_lifecycle_over_http(api_client):
    created = create_campaign(api_client)
    campaign_id = created["id"]

    assert created["status"] == "DRAFT"
    assert created["dialing_mode"] == DialingMode.PROGRESSIVE.value

    started = api_client.post(f"/api/campaigns/{campaign_id}/start").json()
    assert started["status"] == "RUNNING"

    switched = api_client.patch(
        f"/api/campaigns/{campaign_id}/mode",
        json={"dialing_mode": DialingMode.PREDICTIVE.value},
    ).json()
    assert switched["dialing_mode"] == DialingMode.PREDICTIVE.value

    paused = api_client.post(f"/api/campaigns/{campaign_id}/pause").json()
    assert paused["status"] == "PAUSED"

    stopped = api_client.post(f"/api/campaigns/{campaign_id}/stop").json()
    assert stopped["status"] == "STOPPED"


def test_listing_and_fetching_campaigns(api_client):
    created = create_campaign(api_client, name="Listed Campaign")

    listed = api_client.get("/api/campaigns").json()
    fetched = api_client.get(f"/api/campaigns/{created['id']}").json()

    assert any(item["id"] == created["id"] for item in listed)
    assert fetched["name"] == "Listed Campaign"


def test_unknown_campaign_returns_a_not_found_envelope(api_client):
    response = api_client.get("/api/campaigns/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "does-not-exist" in body["error"]["message"]


def test_unknown_campaign_action_is_rejected(api_client):
    created = create_campaign(api_client)

    response = api_client.post(f"/api/campaigns/{created['id']}/explode")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_invalid_campaign_payload_returns_validation_error(api_client):
    response = api_client.post("/api/campaigns", json={"name": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_seed_endpoint_populates_the_campaign(api_client, test_database):
    created = create_campaign(api_client, name="Seeded Campaign")

    response = api_client.post(
        f"/api/campaigns/{created['id']}/seed", json={"agents": 5, "borrowers": 20}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agents_created"] == 5
    assert body["borrowers_created"] == 20


async def test_calls_endpoint_returns_display_ready_records(
    test_database, api_client, call_repository
):
    from tests.conftest import prepare_dialing_call

    context = await prepare_dialing_call(test_database, call_repository)

    listed = api_client.get(f"/api/campaigns/{context.campaign.id}/calls").json()
    detail = api_client.get(f"/api/calls/{context.call.id}").json()

    assert listed["campaign_id"] == context.campaign.id
    assert listed["calls"][0]["id"] == context.call.id
    assert listed["calls"][0]["state"] == CallState.INITIATED.value
    assert detail["call"]["id"] == context.call.id
    assert isinstance(detail["events"], list)


async def test_calls_can_be_filtered_by_state(test_database, api_client, call_repository):
    from tests.conftest import prepare_dialing_call

    context = await prepare_dialing_call(test_database, call_repository)

    matching = api_client.get(
        f"/api/campaigns/{context.campaign.id}/calls",
        params={"state": CallState.INITIATED.value},
    ).json()
    empty = api_client.get(
        f"/api/campaigns/{context.campaign.id}/calls",
        params={"state": CallState.COMPLETED.value},
    ).json()

    assert len(matching["calls"]) == 1
    assert empty["calls"] == []


async def test_metrics_endpoint_reports_live_numbers(test_database, api_client):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 4, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 10)

    metrics = api_client.get(f"/api/campaigns/{campaign.id}/metrics").json()

    assert metrics["campaign_id"] == campaign.id
    assert metrics["agent_states"][AgentState.AVAILABLE.value] == 4
    assert metrics["active_calls"] == 0


async def test_metrics_history_is_available(test_database, api_client, metrics_sampler):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"status": "RUNNING"}}
    )
    await metrics_sampler.sample_once()

    history = api_client.get(f"/api/campaigns/{campaign.id}/metrics/history").json()

    assert len(history) == 1
    assert history[0]["campaign_id"] == campaign.id


def test_provider_health_is_listed(api_client):
    health = api_client.get("/api/providers/health").json()

    names = {item["provider_name"] for item in health}
    assert names == {"mock_a", "mock_b"}
    assert all("status" in item for item in health)
