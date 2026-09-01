import asyncio
from datetime import datetime, timezone

from app.models.enums import CallState
from app.providers.base import ProviderEvent
from app.repositories.agent_repo import AgentRepository
from app.repositories.borrower_repo import BorrowerRepository
from app.repositories.call_repo import CallRepository
from app.repositories.event_repo import EventRepository
from app.services.event_processor import EventProcessor
from app.services.provider_health import ProviderHealthManager
from app.services.retry_service import RetryService
from loadtest.harness import LoadTestResult, Timer, seed_campaign, summarise

DUPLICATE_RATE = 10


async def measure(database, scale: int, settings) -> LoadTestResult:
    campaign = await seed_campaign(database, agents=1, borrowers=1)
    calls = CallRepository(database)
    events = EventRepository(database)
    health = ProviderHealthManager(settings)
    processor = EventProcessor(
        call_repository=calls,
        event_repository=events,
        agent_repository=AgentRepository(database),
        borrower_repository=BorrowerRepository(database),
        retry_service=RetryService(health, settings),
        settings=settings,
    )

    created = []
    for index in range(scale):
        call = await calls.create_call(
            campaign_id=campaign.id,
            agent_id=f"agent-{index}",
            borrower_id=f"borrower-{index}",
            provider_name="mock_a",
            worker_id="worker-1",
        )
        await calls.transition_call(call.id, CallState.RESERVED)
        await calls.transition_call(call.id, CallState.INITIATED)
        await calls.attach_provider_call_id(call.id, f"provider-call-{index}")
        created.append(f"provider-call-{index}")

    payloads = []
    for index, provider_call_id in enumerate(created):
        payloads.append((provider_call_id, f"event-{index}"))
        if index % DUPLICATE_RATE == 0:
            payloads.append((provider_call_id, f"event-{index}"))

    timer = Timer()

    async def process(provider_call_id: str, event_id: str):
        return await timer.measure(
            processor.process_event(
                ProviderEvent(
                    provider_name="mock_a",
                    provider_event_id=event_id,
                    provider_call_id=provider_call_id,
                    event_type="RINGING",
                    provider_timestamp=datetime.now(timezone.utc),
                )
            )
        )

    started = asyncio.get_running_loop().time()
    results = await asyncio.gather(
        *(process(call_id, event_id) for call_id, event_id in payloads)
    )
    duration = asyncio.get_running_loop().time() - started

    duplicates = sum(1 for result in results if result.status.value == "DUPLICATE_IGNORED")
    processed = sum(1 for result in results if result.status.value == "PROCESSED")

    return summarise(
        name="event_processing",
        scale=scale,
        latencies_ms=timer.latencies_ms,
        duration_seconds=duration,
        extra={
            "events_submitted": len(payloads),
            "processed": processed,
            "duplicates_ignored": duplicates,
        },
    )
