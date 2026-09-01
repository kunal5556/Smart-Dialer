from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_campaign
from app.api.schemas import MetricsResponse
from app.metrics.campaign_metrics import CampaignMetrics
from app.models.base import utc_now
from app.models.campaign import Campaign

router = APIRouter(prefix="/api/campaigns", tags=["metrics"])


def to_response(metrics: CampaignMetrics) -> MetricsResponse:
    return MetricsResponse(
        campaign_id=metrics.campaign_id,
        collected_at=metrics.collected_at,
        calls_initiated=metrics.calls_initiated,
        calls_connected=metrics.calls_connected,
        calls_completed=metrics.calls_completed,
        calls_failed=metrics.calls_failed,
        calls_cancelled=metrics.calls_cancelled,
        calls_ringing=metrics.calls_ringing,
        active_calls=metrics.active_calls,
        peak_concurrent_calls=metrics.peak_concurrent_calls,
        answer_rate=metrics.answer_rate,
        average_talk_time_seconds=metrics.average_talk_time_seconds,
        average_setup_time_ms=metrics.average_setup_time_ms,
        agent_states=metrics.agent_states,
        talk_utilization=metrics.talk_utilization,
        productive_utilization=metrics.productive_utilization,
        safety_verdicts=metrics.safety_verdicts,
        progressive_fallbacks=metrics.progressive_fallbacks,
        reservation_contention=metrics.reservation_contention,
        retry_attempts=metrics.retry_attempts,
        provider_failures=metrics.provider_failures,
    )


@router.get("/{campaign_id}/metrics", response_model=MetricsResponse)
async def get_metrics(
    request: Request,
    campaign: Campaign = Depends(require_campaign),
) -> MetricsResponse:
    metrics = await request.app.state.metrics_collector.collect(campaign)
    return to_response(metrics)


@router.get("/{campaign_id}/metrics/history", response_model=list[dict])
async def get_metrics_history(
    request: Request,
    campaign: Campaign = Depends(require_campaign),
    minutes: int = Query(default=30, ge=1, le=1440),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[dict]:
    since = utc_now() - timedelta(minutes=minutes)
    return await request.app.state.metrics_repository.find_history(campaign.id, since, limit)
