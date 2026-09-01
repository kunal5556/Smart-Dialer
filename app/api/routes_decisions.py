from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_campaign
from app.api.schemas import (
    PacingDecisionRecord,
    SafetyConstraintRecordSchema,
    SafetyDecisionRecord,
)
from app.models.campaign import Campaign

router = APIRouter(prefix="/api/campaigns", tags=["decisions"])


@router.get("/{campaign_id}/pacing-decisions", response_model=list[PacingDecisionRecord])
async def list_pacing_decisions(
    request: Request,
    campaign: Campaign = Depends(require_campaign),
    limit: int = Query(default=20, ge=1, le=200),
) -> list[PacingDecisionRecord]:
    decisions = await request.app.state.decision_repository.find_recent_pacing_decisions(
        campaign.id, limit
    )
    return [
        PacingDecisionRecord(
            id=decision.id,
            campaign_id=decision.campaign_id,
            dialing_mode=decision.dialing_mode.value,
            requested=decision.requested,
            explanation=decision.explanation,
            inputs=decision.inputs,
            created_at=decision.created_at,
        )
        for decision in decisions
    ]


@router.get("/{campaign_id}/safety-decisions", response_model=list[SafetyDecisionRecord])
async def list_safety_decisions(
    request: Request,
    campaign: Campaign = Depends(require_campaign),
    limit: int = Query(default=20, ge=1, le=200),
) -> list[SafetyDecisionRecord]:
    decisions = await request.app.state.decision_repository.find_recent_safety_decisions(
        campaign.id, limit
    )
    return [
        SafetyDecisionRecord(
            id=decision.id,
            campaign_id=decision.campaign_id,
            pacing_decision_id=decision.pacing_decision_id,
            requested=decision.requested,
            approved=decision.approved,
            verdict=decision.verdict.value,
            binding_constraint=decision.binding_constraint,
            snapshot_age_ms=decision.snapshot_age_ms,
            constraints=[
                SafetyConstraintRecordSchema(
                    name=constraint.name,
                    limit=constraint.limit,
                    value=constraint.value,
                    binding=constraint.binding,
                )
                for constraint in decision.constraints
            ],
            created_at=decision.created_at,
        )
        for decision in decisions
    ]
