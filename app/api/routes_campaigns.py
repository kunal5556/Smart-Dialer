from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_campaign_repository, require_api_key, require_campaign
from app.api.schemas import CampaignSummary, CreateCampaignRequest, SeedRequest, SetModeRequest
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign, PacingConfig
from app.models.enums import CampaignStatus
from app.repositories.campaign_repo import CampaignRepository

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

STATUS_ACTIONS = {
    "start": CampaignStatus.RUNNING,
    "pause": CampaignStatus.PAUSED,
    "stop": CampaignStatus.STOPPED,
}


def to_summary(campaign: Campaign) -> CampaignSummary:
    return CampaignSummary(
        id=campaign.id,
        name=campaign.name,
        status=campaign.status.value,
        dialing_mode=campaign.dialing_mode.value,
        provider_name=campaign.provider_name,
        max_concurrent_calls=campaign.max_concurrent_calls,
        baseline_answer_rate=campaign.pacing_config.baseline_answer_rate,
        created_at=campaign.created_at,
    )


@router.get("", response_model=list[CampaignSummary])
async def list_campaigns(
    campaigns: CampaignRepository = Depends(get_campaign_repository),
) -> list[CampaignSummary]:
    return [to_summary(campaign) for campaign in await campaigns.find_all()]


@router.get("/{campaign_id}", response_model=CampaignSummary)
async def get_campaign(campaign: Campaign = Depends(require_campaign)) -> CampaignSummary:
    return to_summary(campaign)


@router.post("", response_model=CampaignSummary, dependencies=[Depends(require_api_key)])
async def create_campaign(
    payload: CreateCampaignRequest,
    campaigns: CampaignRepository = Depends(get_campaign_repository),
) -> CampaignSummary:
    campaign = Campaign(
        name=payload.name,
        dialing_mode=payload.dialing_mode,
        provider_name=payload.provider_name,
        max_concurrent_calls=payload.max_concurrent_calls,
        pacing_config=PacingConfig(baseline_answer_rate=payload.baseline_answer_rate),
    )
    await campaigns.insert(campaign)
    return to_summary(campaign)


@router.post(
    "/{campaign_id}/{action}",
    response_model=CampaignSummary,
    dependencies=[Depends(require_api_key)],
)
async def change_status(
    action: str,
    campaign: Campaign = Depends(require_campaign),
    campaigns: CampaignRepository = Depends(get_campaign_repository),
) -> CampaignSummary:
    from app.api.errors import NotFoundError

    target = STATUS_ACTIONS.get(action)
    if target is None:
        raise NotFoundError("campaign action", action)
    updated = await campaigns.set_status(campaign.id, target)
    return to_summary(updated)


@router.patch(
    "/{campaign_id}/mode",
    response_model=CampaignSummary,
    dependencies=[Depends(require_api_key)],
)
async def set_mode(
    payload: SetModeRequest,
    campaign: Campaign = Depends(require_campaign),
    campaigns: CampaignRepository = Depends(get_campaign_repository),
) -> CampaignSummary:
    updated = await campaigns.set_dialing_mode(campaign.id, payload.dialing_mode)
    return to_summary(updated)


@router.post(
    "/{campaign_id}/seed",
    response_model=dict,
    dependencies=[Depends(require_api_key)],
)
async def seed_demo_data(
    payload: SeedRequest,
    request: Request,
    campaign: Campaign = Depends(require_campaign),
) -> dict:
    agents_repo = request.app.state.agent_repository
    borrowers_repo = request.app.state.borrower_repository

    agents = [
        Agent(campaign_id=campaign.id, name=f"Agent {number:04d}")
        for number in range(1, payload.agents + 1)
    ]
    borrowers = [
        Borrower(
            campaign_id=campaign.id,
            name=f"Borrower {number:05d}",
            phone_number=f"+1555{number:07d}",
        )
        for number in range(1, payload.borrowers + 1)
    ]
    await agents_repo.insert_many(agents)
    await borrowers_repo.insert_many(borrowers)
    return {
        "campaign_id": campaign.id,
        "agents_created": len(agents),
        "borrowers_created": len(borrowers),
    }
