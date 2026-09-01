import pytest
from streamlit.testing.v1 import AppTest

from dashboard.api_client import ApiError, ApiUnreachable, MissingConfiguration, SmartDialerClient

import pathlib

APP_PATH = str(pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
DEFAULT_TIMEOUT = 30

CAMPAIGN = {
    "id": "campaign-1",
    "name": "Demo Campaign",
    "status": "RUNNING",
    "dialing_mode": "PROGRESSIVE",
    "provider_name": "mock_a",
    "max_concurrent_calls": 50,
    "baseline_answer_rate": 0.3,
    "created_at": "2026-01-01T00:00:00+00:00",
}

AGENTS = {
    "campaign_id": "campaign-1",
    "state_summary": {
        "OFFLINE": 1,
        "AVAILABLE": 3,
        "RESERVED": 0,
        "DIALING": 1,
        "CONNECTED": 1,
        "WRAP_UP": 0,
        "PAUSED": 0,
    },
    "agents": [
        {
            "id": "agent-1",
            "name": "Agent 001",
            "state": "AVAILABLE",
            "state_version": 3,
            "reserved_by": None,
            "lease_expires_at": None,
            "current_call_id": None,
            "last_heartbeat_at": "2026-01-01T00:00:05+00:00",
            "state_changed_at": "2026-01-01T00:00:05+00:00",
        }
    ],
}

CALLS = {
    "campaign_id": "campaign-1",
    "state_filter": None,
    "calls": [
        {
            "id": "call-abcdefgh",
            "campaign_id": "campaign-1",
            "agent_id": "agent-1",
            "borrower_id": "borrower-1",
            "state": "COMPLETED",
            "provider_name": "mock_a",
            "provider_call_id": "mock_a-call-1",
            "attempt": 1,
            "failure_reason": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "ended_at": "2026-01-01T00:02:00+00:00",
            "duration_seconds": 120.0,
        }
    ],
}

CALL_DETAIL = {
    "call": CALLS["calls"][0],
    "events": [
        {
            "provider_event_id": "event-1",
            "provider_name": "mock_a",
            "event_type": "ANSWERED",
            "processing_status": "PROCESSED",
            "applied_transition": "RINGING->ANSWERED",
            "received_at": "2026-01-01T00:00:10+00:00",
        },
        {
            "provider_event_id": "event-2",
            "provider_name": "mock_a",
            "event_type": "ANSWERED",
            "processing_status": "DUPLICATE_IGNORED",
            "applied_transition": None,
            "received_at": "2026-01-01T00:00:11+00:00",
        },
    ],
}

PACING_DECISIONS = [
    {
        "id": "pacing-1",
        "campaign_id": "campaign-1",
        "dialing_mode": "PREDICTIVE",
        "requested": 17,
        "explanation": "12 agents free ... = 17 requested.",
        "inputs": {
            "available_agents": 12,
            "soon_free_agents": 3,
            "free_capacity": 13.5,
            "effective_answer_rate": 0.32,
            "calls_needed": 42.1875,
            "in_flight": 21,
            "raw_request": 21,
            "safety_margin": 0.85,
            "health_factor": 1.0,
            "volatility_factor": 1.0,
            "requested": 17,
        },
        "created_at": "2026-01-01T00:00:20+00:00",
    }
]

SAFETY_DECISIONS = [
    {
        "id": "safety-1",
        "campaign_id": "campaign-1",
        "pacing_decision_id": "pacing-1",
        "requested": 17,
        "approved": 8,
        "verdict": "REDUCED",
        "binding_constraint": "agent_capacity",
        "snapshot_age_ms": 12,
        "constraints": [
            {"name": "agent_capacity", "limit": 8, "value": 8.0, "binding": True},
            {"name": "campaign_concurrency", "limit": 50, "value": 2.0, "binding": False},
        ],
        "created_at": "2026-01-01T00:00:20+00:00",
    }
]

METRICS = {
    "campaign_id": "campaign-1",
    "collected_at": "2026-01-01T00:00:30+00:00",
    "calls_initiated": 41,
    "calls_connected": 2,
    "calls_completed": 9,
    "calls_failed": 22,
    "calls_cancelled": 0,
    "calls_ringing": 3,
    "active_calls": 5,
    "peak_concurrent_calls": 10,
    "answer_rate": 0.22,
    "average_talk_time_seconds": 118.0,
    "average_setup_time_ms": 205.0,
    "agent_states": AGENTS["state_summary"],
    "talk_utilization": 0.78,
    "productive_utilization": 0.81,
    "safety_verdicts": {"APPROVED": 1, "REDUCED": 12, "REJECTED": 0, "FALLBACK_PROGRESSIVE": 2},
    "progressive_fallbacks": 2,
    "reservation_contention": 4,
    "retry_attempts": 6,
    "provider_failures": 3,
}

METRICS_HISTORY = [
    {**METRICS, "collected_at": "2026-01-01T00:00:10+00:00"},
    {**METRICS, "collected_at": "2026-01-01T00:00:20+00:00"},
]

PROVIDER_HEALTH = [
    {
        "provider_name": "mock_a",
        "status": "HEALTHY",
        "request_count": 40,
        "success_rate": 0.98,
        "failure_rate": 0.02,
        "timeout_rate": 0.0,
        "p50_latency_ms": 180.0,
        "p95_latency_ms": 240.0,
        "consecutive_failures": 0,
        "events_received": 120,
        "low_confidence": False,
        "computed_at": "2026-01-01T00:00:30+00:00",
    }
]

SIMULATIONS = [
    {
        "id": "sim-12345678",
        "scenario": "A",
        "dialing_mode": "PROGRESSIVE",
        "status": "COMPLETED",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:30+00:00",
        "passed": True,
        "violations": [],
        "error": None,
        "metrics": METRICS,
    }
]


class StubClient:
    def __init__(self, **overrides):
        self.base_url = "https://stub.example.com"
        self.calls: list[tuple[str, tuple]] = []
        self._overrides = overrides

    def _record(self, name: str, *args):
        self.calls.append((name, args))
        if name in self._overrides:
            value = self._overrides[name]
            if isinstance(value, Exception):
                raise value
            return value
        return None

    def list_campaigns(self):
        result = self._record("list_campaigns")
        return [CAMPAIGN] if result is None else result

    def get_agents(self, campaign_id):
        result = self._record("get_agents", campaign_id)
        return AGENTS if result is None else result

    def get_calls(self, campaign_id, state=None, limit=50):
        result = self._record("get_calls", campaign_id, state, limit)
        return CALLS if result is None else result

    def get_call_detail(self, call_id):
        result = self._record("get_call_detail", call_id)
        return CALL_DETAIL if result is None else result

    def get_pacing_decisions(self, campaign_id, limit=10):
        result = self._record("get_pacing_decisions", campaign_id, limit)
        return PACING_DECISIONS if result is None else result

    def get_safety_decisions(self, campaign_id, limit=10):
        result = self._record("get_safety_decisions", campaign_id, limit)
        return SAFETY_DECISIONS if result is None else result

    def get_metrics(self, campaign_id):
        result = self._record("get_metrics", campaign_id)
        return METRICS if result is None else result

    def get_metrics_history(self, campaign_id, minutes=30):
        result = self._record("get_metrics_history", campaign_id, minutes)
        return METRICS_HISTORY if result is None else result

    def get_provider_health(self):
        result = self._record("get_provider_health")
        return PROVIDER_HEALTH if result is None else result

    def list_simulations(self):
        result = self._record("list_simulations")
        return SIMULATIONS if result is None else result

    def start_campaign(self, campaign_id):
        result = self._record("start_campaign", campaign_id)
        return CAMPAIGN if result is None else result

    def pause_campaign(self, campaign_id):
        return self._record("pause_campaign", campaign_id) or CAMPAIGN

    def stop_campaign(self, campaign_id):
        return self._record("stop_campaign", campaign_id) or CAMPAIGN

    def set_mode(self, campaign_id, dialing_mode):
        return self._record("set_mode", campaign_id, dialing_mode) or CAMPAIGN

    def seed_campaign(self, campaign_id, agents, borrowers):
        return self._record("seed_campaign", campaign_id, agents, borrowers) or {}

    def set_provider_outage(self, provider_name, seconds):
        return self._record("set_provider_outage", provider_name, seconds) or PROVIDER_HEALTH[0]

    def start_simulation(self, payload):
        return self._record("start_simulation", payload) or SIMULATIONS[0]

    def inject_fault(self, payload):
        result = self._record("inject_fault", payload)
        return result or {"fault": payload["fault"], "detail": "applied", "affected": 1}

    def method_names(self) -> list[str]:
        return [name for name, _ in self.calls]


def install_stub(monkeypatch, client: StubClient) -> StubClient:
    import streamlit as st

    import dashboard.api_client as api_client_module

    st.cache_resource.clear()
    monkeypatch.setattr(
        api_client_module, "SmartDialerClient", lambda *args, **kwargs: client
    )
    monkeypatch.setenv("SD_API_BASE_URL", "https://stub.example.com")
    return client


def run_app(stub: StubClient) -> AppTest:
    app = AppTest.from_file(APP_PATH, default_timeout=DEFAULT_TIMEOUT)
    app.run()
    return app


@pytest.fixture
def stub(monkeypatch) -> StubClient:
    return install_stub(monkeypatch, StubClient())


def test_the_app_renders_every_tab_without_exceptions(stub):
    app = run_app(stub)

    assert not app.exception
    assert app.title[0].value == "SmartDialer"
    assert len(app.tabs) >= 7


def test_every_panel_pulls_its_data_from_the_api(stub):
    run_app(stub)

    called = stub.method_names()
    assert "list_campaigns" in called
    assert "get_agents" in called
    assert "get_calls" in called
    assert "get_metrics" in called
    assert "get_pacing_decisions" in called
    assert "get_safety_decisions" in called
    assert "get_provider_health" in called
    assert "list_simulations" in called


def test_the_safety_panel_shows_requested_against_approved(stub):
    app = run_app(stub)

    labels = [metric.label for metric in app.metric]
    assert "Requested" in labels
    assert "Approved" in labels


def test_the_pacing_panel_shows_the_explanation(stub):
    app = run_app(stub)

    messages = [element.value for element in app.info]
    assert any("17 requested" in message for message in messages)


def test_the_event_trail_is_rendered(stub):
    app = run_app(stub)

    assert not app.exception
    assert "get_call_detail" in stub.method_names()


def test_an_empty_campaign_list_shows_a_prompt(monkeypatch):
    client = install_stub(monkeypatch, StubClient(list_campaigns=[]))

    app = AppTest.from_file(APP_PATH, default_timeout=DEFAULT_TIMEOUT)
    app.run()

    assert any("No campaigns yet" in element.value for element in app.info)


def test_an_unreachable_backend_is_reported_clearly(monkeypatch):
    client = install_stub(monkeypatch, StubClient(list_campaigns=ApiUnreachable("https://stub.example.com", "connection refused")))

    app = AppTest.from_file(APP_PATH, default_timeout=DEFAULT_TIMEOUT)
    app.run()

    assert any("Could not reach" in element.value for element in app.error)


def test_a_missing_api_key_gets_a_specific_message(monkeypatch):
    client = install_stub(monkeypatch, StubClient(get_agents=ApiError(401, "unauthorized", "A valid X-API-Key header is required")))

    app = AppTest.from_file(APP_PATH, default_timeout=DEFAULT_TIMEOUT)
    app.run()

    assert any("API key" in element.value for element in app.error)


def test_one_failing_panel_does_not_blank_the_page(monkeypatch):
    client = install_stub(monkeypatch, StubClient(get_provider_health=RuntimeError("provider panel exploded")))

    app = AppTest.from_file(APP_PATH, default_timeout=DEFAULT_TIMEOUT)
    app.run()

    assert not app.exception
    assert any("provider panel exploded" in element.value for element in app.error)
    assert "get_metrics" in client.method_names()


def test_missing_base_url_raises_a_clear_configuration_error():
    with pytest.raises(MissingConfiguration) as error:
        SmartDialerClient(base_url="")

    assert "SD_API_BASE_URL" in str(error.value)


def test_the_client_sends_the_api_key_header():
    client = SmartDialerClient(base_url="https://example.com", api_key="secret")

    assert client._session.headers["X-API-Key"] == "secret"
    client.close()


def test_the_client_has_no_default_base_url():
    import inspect

    source = inspect.getsource(SmartDialerClient.__init__)

    assert "http://" not in source
    assert "localhost" not in source
