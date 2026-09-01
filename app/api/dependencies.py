import time

from fastapi import Depends, Header, Request

from app.api.errors import NotFoundError, RateLimitedError, UnauthorizedError
from app.config import Settings, get_settings
from app.models.campaign import Campaign
from app.repositories.campaign_repo import CampaignRepository

FAULT_COOLDOWN_SECONDS = 5.0

_fault_cooldowns: dict[str, float] = {}


def get_runtime(request: Request):
    return request.app.state


def get_campaign_repository(request: Request) -> CampaignRepository:
    return request.app.state.campaign_repository


async def require_campaign(
    campaign_id: str,
    campaigns: CampaignRepository = Depends(get_campaign_repository),
) -> Campaign:
    campaign = await campaigns.find_by_id(campaign_id)
    if campaign is None:
        raise NotFoundError("campaign", campaign_id)
    return campaign


def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.API_KEY:
        return
    if x_api_key != settings.API_KEY:
        raise UnauthorizedError()


def enforce_fault_cooldown(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    last_call = _fault_cooldowns.get(client)
    if last_call is not None and now - last_call < FAULT_COOLDOWN_SECONDS:
        raise RateLimitedError(FAULT_COOLDOWN_SECONDS - (now - last_call))
    _fault_cooldowns[client] = now


def reset_fault_cooldowns() -> None:
    _fault_cooldowns.clear()
