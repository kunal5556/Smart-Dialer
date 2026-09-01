import os

import requests

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0
TIMEOUT = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)

BASE_URL_SECRET = "SD_API_BASE_URL"
API_KEY_SECRET = "SD_API_KEY"


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class ApiUnreachable(ApiError):
    def __init__(self, base_url: str, reason: str) -> None:
        super().__init__(
            status_code=0,
            code="unreachable",
            message=f"Could not reach the SmartDialer API at {base_url}: {reason}",
        )


class MissingConfiguration(Exception):
    pass


def resolve_setting(name: str, secrets: dict | None) -> str | None:
    if secrets is not None and name in secrets:
        return str(secrets[name])
    return os.environ.get(name)


class SmartDialerClient:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        if not base_url:
            raise MissingConfiguration(
                f"{BASE_URL_SECRET} is not set. Add it to .streamlit/secrets.toml "
                "or export it as an environment variable."
            )
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        if api_key:
            self._session.headers["X-API-Key"] = api_key

    def close(self) -> None:
        self._session.close()

    def list_campaigns(self) -> list[dict]:
        return self._request("GET", "/api/campaigns")

    def get_campaign(self, campaign_id: str) -> dict:
        return self._request("GET", f"/api/campaigns/{campaign_id}")

    def create_campaign(self, payload: dict) -> dict:
        return self._request("POST", "/api/campaigns", json=payload)

    def start_campaign(self, campaign_id: str) -> dict:
        return self._request("POST", f"/api/campaigns/{campaign_id}/start")

    def pause_campaign(self, campaign_id: str) -> dict:
        return self._request("POST", f"/api/campaigns/{campaign_id}/pause")

    def stop_campaign(self, campaign_id: str) -> dict:
        return self._request("POST", f"/api/campaigns/{campaign_id}/stop")

    def set_mode(self, campaign_id: str, dialing_mode: str) -> dict:
        return self._request(
            "PATCH",
            f"/api/campaigns/{campaign_id}/mode",
            json={"dialing_mode": dialing_mode},
        )

    def seed_campaign(self, campaign_id: str, agents: int, borrowers: int) -> dict:
        return self._request(
            "POST",
            f"/api/campaigns/{campaign_id}/seed",
            json={"agents": agents, "borrowers": borrowers},
        )

    def get_agents(self, campaign_id: str) -> dict:
        return self._request("GET", f"/api/campaigns/{campaign_id}/agents")

    def agent_action(self, agent_id: str, action: str) -> dict:
        return self._request("POST", f"/api/agents/{agent_id}/{action}")

    def get_calls(self, campaign_id: str, state: str | None = None, limit: int = 50) -> dict:
        params: dict = {"limit": limit}
        if state:
            params["state"] = state
        return self._request("GET", f"/api/campaigns/{campaign_id}/calls", params=params)

    def get_call_detail(self, call_id: str) -> dict:
        return self._request("GET", f"/api/calls/{call_id}")

    def get_pacing_decisions(self, campaign_id: str, limit: int = 10) -> list[dict]:
        return self._request(
            "GET", f"/api/campaigns/{campaign_id}/pacing-decisions", params={"limit": limit}
        )

    def get_safety_decisions(self, campaign_id: str, limit: int = 10) -> list[dict]:
        return self._request(
            "GET", f"/api/campaigns/{campaign_id}/safety-decisions", params={"limit": limit}
        )

    def get_metrics(self, campaign_id: str) -> dict:
        return self._request("GET", f"/api/campaigns/{campaign_id}/metrics")

    def get_metrics_history(self, campaign_id: str, minutes: int = 30) -> list[dict]:
        return self._request(
            "GET",
            f"/api/campaigns/{campaign_id}/metrics/history",
            params={"minutes": minutes},
        )

    def get_provider_health(self) -> list[dict]:
        return self._request("GET", "/api/providers/health")

    def set_provider_outage(self, provider_name: str, seconds: float) -> dict:
        return self._request(
            "POST", f"/api/providers/{provider_name}/outage", json={"seconds": seconds}
        )

    def start_simulation(self, payload: dict) -> dict:
        return self._request("POST", "/api/simulations", json=payload)

    def get_simulation(self, simulation_id: str) -> dict:
        return self._request("GET", f"/api/simulations/{simulation_id}")

    def list_simulations(self) -> list[dict]:
        return self._request("GET", "/api/simulations")

    def inject_fault(self, payload: dict) -> dict:
        return self._request("POST", "/api/simulations/faults", json=payload)

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            response = self._session.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.Timeout as error:
            raise ApiUnreachable(self.base_url, f"timed out ({error})") from error
        except requests.RequestException as error:
            raise ApiUnreachable(self.base_url, str(error)) from error

        if response.status_code >= 400:
            raise self._to_error(response)
        return response.json()

    def _to_error(self, response: requests.Response) -> ApiError:
        try:
            envelope = response.json().get("error", {})
        except ValueError:
            envelope = {}
        return ApiError(
            status_code=response.status_code,
            code=envelope.get("code", "http_error"),
            message=envelope.get("message", response.reason or "Request failed"),
            details=envelope.get("details", {}),
        )
