import time

import pytest

from app.api.dependencies import reset_fault_cooldowns
from app.models.enums import AgentState
from tests.conftest import insert_agents, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

SIMULATION_PAYLOAD = {
    "scenario": "A",
    "dialing_mode": "PROGRESSIVE",
    "agents": 3,
    "borrowers": 30,
    "duration_seconds": 60.0,
    "time_scale": 300.0,
    "seed": 99,
}


def wait_for_completion(client, simulation_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/simulations/{simulation_id}").json()
        if body["status"] != "RUNNING":
            return body
        time.sleep(0.2)
    raise AssertionError("simulation did not finish in time")


def test_simulation_runs_end_to_end(api_client):
    started = api_client.post("/api/simulations", json=SIMULATION_PAYLOAD)

    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "RUNNING"
    assert body["scenario"] == "A"

    finished = wait_for_completion(api_client, body["id"])

    assert finished["status"] == "COMPLETED"
    assert finished["passed"] is True
    assert finished["violations"] == []
    assert finished["metrics"]["campaign_id"]


def test_simulation_history_is_listed(api_client):
    started = api_client.post("/api/simulations", json=SIMULATION_PAYLOAD).json()
    wait_for_completion(api_client, started["id"])

    history = api_client.get("/api/simulations").json()

    assert any(item["id"] == started["id"] for item in history)


def test_two_simulations_cannot_run_at_once(api_client):
    first = api_client.post("/api/simulations", json=SIMULATION_PAYLOAD).json()
    second = api_client.post("/api/simulations", json=SIMULATION_PAYLOAD)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"

    wait_for_completion(api_client, first["id"])


def test_unknown_simulation_is_not_found(api_client):
    response = api_client.get("/api/simulations/missing")

    assert response.status_code == 404


def test_unknown_scenario_is_rejected(api_client):
    response = api_client.post(
        "/api/simulations", json={**SIMULATION_PAYLOAD, "scenario": "Z"}
    )

    assert response.status_code == 404


def test_invalid_simulation_parameters_are_rejected(api_client):
    response = api_client.post("/api/simulations", json={**SIMULATION_PAYLOAD, "agents": 0})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "fault",
    [
        "provider_outage",
        "provider_latency_spike",
        "duplicate_event_burst",
        "out_of_order_burst",
    ],
)
def test_each_fault_can_be_injected(api_client, fault):
    reset_fault_cooldowns()
    response = api_client.post("/api/simulations/faults", json={"fault": fault})
    reset_fault_cooldowns()

    assert response.status_code == 200
    body = response.json()
    assert body["fault"] == fault
    assert "detail" in body


async def test_agent_availability_drop_fault_needs_a_campaign(test_database, api_client):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)

    reset_fault_cooldowns()
    missing_campaign = api_client.post(
        "/api/simulations/faults",
        json={"fault": "agent_availability_drop", "agents_offline": 4},
    )
    reset_fault_cooldowns()
    with_campaign = api_client.post(
        "/api/simulations/faults",
        json={
            "fault": "agent_availability_drop",
            "agents_offline": 4,
            "campaign_id": campaign.id,
        },
    )
    reset_fault_cooldowns()

    assert missing_campaign.status_code == 409
    assert with_campaign.status_code == 200
    assert with_campaign.json()["affected"] == 4


def test_unknown_fault_is_rejected(api_client):
    reset_fault_cooldowns()
    response = api_client.post("/api/simulations/faults", json={"fault": "meteor_strike"})
    reset_fault_cooldowns()

    assert response.status_code == 404
