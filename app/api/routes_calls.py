from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_campaign
from app.api.errors import NotFoundError
from app.api.schemas import CallDetailResponse, CallListResponse, CallRecord, ProviderEventRecord
from app.models.call import Call
from app.models.campaign import Campaign
from app.models.enums import CallState

router = APIRouter(prefix="/api", tags=["calls"])


def to_record(call: Call) -> CallRecord:
    duration = None
    if call.answered_at is not None and call.ended_at is not None:
        duration = (call.ended_at - call.answered_at).total_seconds()
    return CallRecord(
        id=call.id,
        campaign_id=call.campaign_id,
        agent_id=call.agent_id,
        borrower_id=call.borrower_id,
        state=call.state.value,
        provider_name=call.provider_name,
        provider_call_id=call.provider_call_id,
        attempt=call.attempt,
        failure_reason=call.failure_reason,
        created_at=call.created_at,
        ended_at=call.ended_at,
        duration_seconds=duration,
    )


@router.get("/campaigns/{campaign_id}/calls", response_model=CallListResponse)
async def list_calls(
    request: Request,
    campaign: Campaign = Depends(require_campaign),
    state: CallState | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> CallListResponse:
    calls = await request.app.state.call_repository.find_recent(
        campaign_id=campaign.id, limit=limit, state=state
    )
    return CallListResponse(
        campaign_id=campaign.id,
        state_filter=state,
        calls=[to_record(call) for call in calls],
    )


@router.get("/calls/{call_id}", response_model=CallDetailResponse)
async def get_call(call_id: str, request: Request) -> CallDetailResponse:
    call = await request.app.state.call_repository.find_by_id(call_id)
    if call is None:
        raise NotFoundError("call", call_id)

    events = []
    if call.provider_call_id is not None:
        events = await request.app.state.event_repository.find_for_call(call.provider_call_id)

    return CallDetailResponse(
        call=to_record(call),
        events=[
            ProviderEventRecord(
                provider_event_id=event.provider_event_id,
                provider_name=event.provider_name,
                event_type=event.event_type,
                processing_status=(
                    event.processing_status.value if event.processing_status else None
                ),
                applied_transition=event.applied_transition,
                received_at=event.received_at,
            )
            for event in events
        ],
    )
