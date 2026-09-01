from fastapi import APIRouter, Depends, Request

from app.api.dependencies import enforce_fault_cooldown, require_api_key
from app.api.errors import ConflictError, NotFoundError
from app.api.schemas import OutageRequest, ProviderHealthRecord
from app.providers.errors import ProviderUnavailable
from app.providers.mock_b import MockProviderB
from app.services.provider_health import ProviderHealth

router = APIRouter(prefix="/api/providers", tags=["providers"])


def to_record(health: ProviderHealth) -> ProviderHealthRecord:
    return ProviderHealthRecord(
        provider_name=health.provider_name,
        status=health.status.value,
        request_count=health.request_count,
        success_rate=health.success_rate,
        failure_rate=health.failure_rate,
        timeout_rate=health.timeout_rate,
        p50_latency_ms=health.p50_latency_ms,
        p95_latency_ms=health.p95_latency_ms,
        consecutive_failures=health.consecutive_failures,
        events_received=health.events_received,
        low_confidence=health.low_confidence,
        computed_at=health.computed_at,
    )


@router.get("/health", response_model=list[ProviderHealthRecord])
async def get_provider_health(request: Request) -> list[ProviderHealthRecord]:
    registry = request.app.state.provider_registry
    manager = request.app.state.health_manager
    return [to_record(manager.get_health(name)) for name in registry.names()]


@router.post(
    "/{provider_name}/outage",
    response_model=ProviderHealthRecord,
    dependencies=[Depends(require_api_key), Depends(enforce_fault_cooldown)],
)
async def start_outage(
    provider_name: str,
    payload: OutageRequest,
    request: Request,
) -> ProviderHealthRecord:
    registry = request.app.state.provider_registry
    try:
        provider = registry.get(provider_name)
    except ProviderUnavailable as error:
        raise NotFoundError("provider", provider_name) from error

    if not isinstance(provider, MockProviderB):
        raise ConflictError(
            f"Provider {provider_name} does not support forced outages",
            {"provider_name": provider_name},
        )

    if payload.seconds > 0:
        provider.force_outage(payload.seconds)
    else:
        provider.clear_outage()

    return to_record(request.app.state.health_manager.get_health(provider_name))
