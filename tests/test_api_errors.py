import pytest

from tests.conftest import API_TEST_KEY, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


def test_error_envelope_is_consistent_across_error_types(api_client):
    not_found = api_client.get("/api/campaigns/missing").json()
    validation = api_client.post("/api/campaigns", json={"name": ""}).json()

    for body in (not_found, validation):
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "details"}
        assert isinstance(body["error"]["message"], str)


def test_no_stack_trace_is_returned_to_the_client(api_client):
    body = api_client.get("/api/campaigns/missing").text

    assert "Traceback" not in body
    assert "File \"" not in body


def test_mutating_endpoints_require_the_api_key_when_configured(api_client_with_key):
    response = api_client_with_key.post("/api/campaigns", json={"name": "Blocked"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_read_endpoints_stay_open_when_the_api_key_is_configured(api_client_with_key):
    response = api_client_with_key.get("/api/campaigns")

    assert response.status_code == 200


def test_a_correct_api_key_is_accepted(api_client_with_key):
    response = api_client_with_key.post(
        "/api/campaigns",
        json={"name": "Allowed"},
        headers={"X-API-Key": API_TEST_KEY},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Allowed"


def test_a_wrong_api_key_is_rejected(api_client_with_key):
    response = api_client_with_key.post(
        "/api/campaigns",
        json={"name": "Rejected"},
        headers={"X-API-Key": "wrong"},
    )

    assert response.status_code == 401


def test_mutating_endpoints_are_open_when_no_key_is_configured(api_client):
    response = api_client.post("/api/campaigns", json={"name": "Open"})

    assert response.status_code == 200


def test_cors_preflight_succeeds_for_a_configured_origin(api_client):
    from app.config import get_settings

    origin = get_settings().cors_origins[0]
    response = api_client.options(
        "/api/campaigns",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_health_endpoint_still_works_alongside_the_api(api_client):
    body = api_client.get("/health").json()

    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_fault_injection_is_rate_limited(api_client):
    from app.api.dependencies import reset_fault_cooldowns

    reset_fault_cooldowns()
    first = api_client.post(
        "/api/simulations/faults", json={"fault": "provider_outage", "seconds": 1}
    )
    second = api_client.post(
        "/api/simulations/faults", json={"fault": "provider_outage", "seconds": 1}
    )
    reset_fault_cooldowns()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
