import pytest

from app.models.enums import AgentState, DialingMode
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

REQUIRED_EXPLAINABILITY_FIELDS = [
    "available_agents",
    "active_calls",
    "ringing_calls",
    "historical_answer_rate",
    "effective_answer_rate",
    "avg_talk_time_seconds",
    "avg_setup_time_ms",
    "provider_status",
    "safety_margin",
    "calls_needed",
    "requested",
]


async def run_one_tick(mode_router, campaign):
    return await mode_router.select(campaign).tick(campaign, "worker-1")


async def test_pacing_decisions_expose_every_explainability_field(
    test_database, api_client, mode_router
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 20)
    await run_one_tick(mode_router, campaign)

    decisions = api_client.get(f"/api/campaigns/{campaign.id}/pacing-decisions").json()

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["explanation"]
    assert decision["dialing_mode"] == DialingMode.PROGRESSIVE.value
    for field in REQUIRED_EXPLAINABILITY_FIELDS:
        assert field in decision["inputs"]


async def test_safety_decisions_expose_the_constraint_breakdown(
    test_database, api_client, mode_router
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 20)
    await run_one_tick(mode_router, campaign)

    decisions = api_client.get(f"/api/campaigns/{campaign.id}/safety-decisions").json()

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["requested"] >= decision["approved"]
    assert len(decision["constraints"]) == 8
    assert {"name", "limit", "value", "binding"} <= set(decision["constraints"][0])
    assert decision["snapshot_age_ms"] is not None


async def test_decision_pair_is_linked_for_the_dashboard(
    test_database, api_client, mode_router
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 10)
    await run_one_tick(mode_router, campaign)

    pacing = api_client.get(f"/api/campaigns/{campaign.id}/pacing-decisions").json()
    safety = api_client.get(f"/api/campaigns/{campaign.id}/safety-decisions").json()

    assert safety[0]["pacing_decision_id"] == pacing[0]["id"]


async def test_decisions_are_limited_and_newest_first(
    test_database, api_client, mode_router
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 30)
    for _ in range(3):
        await run_one_tick(mode_router, campaign)

    decisions = api_client.get(
        f"/api/campaigns/{campaign.id}/pacing-decisions", params={"limit": 2}
    ).json()

    assert len(decisions) == 2
    assert decisions[0]["created_at"] >= decisions[1]["created_at"]


def test_decisions_for_an_unknown_campaign_are_not_found(api_client):
    response = api_client.get("/api/campaigns/missing/pacing-decisions")

    assert response.status_code == 404
