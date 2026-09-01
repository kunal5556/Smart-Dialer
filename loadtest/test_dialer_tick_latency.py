import asyncio

from app.dialers.progressive_dialer import ProgressiveDialer
from app.metrics.registry import MetricsRegistry
from app.pacing.metrics_snapshot import MetricsSnapshotBuilder
from app.providers.base import ProviderEvent
from app.providers.registry import build_registry
from app.repositories.agent_repo import AgentRepository
from app.repositories.borrower_repo import BorrowerRepository
from app.repositories.call_repo import CallRepository
from app.repositories.decision_repo import DecisionRepository
from app.safety.safety_controller import SafetyController
from app.services.agent_availability import AgentAvailabilityTracker
from app.services.call_allocator import CallAllocator
from app.services.provider_health import ProviderHealthManager
from app.services.reservation_service import ReservationService
from app.services.retry_service import RetryService
from loadtest.harness import LoadTestResult, Timer, seed_campaign, summarise

TICKS = 5


async def measure(database, scale: int, settings) -> LoadTestResult:
    campaign = await seed_campaign(database, agents=scale, borrowers=scale * 2)

    agents = AgentRepository(database)
    borrowers = BorrowerRepository(database)
    calls = CallRepository(database)
    decisions = DecisionRepository(database)
    counters = MetricsRegistry()
    health = ProviderHealthManager(settings)
    retries = RetryService(health, settings)

    async def on_event(event: ProviderEvent) -> None:
        return None

    registry = build_registry(on_event=on_event, seed=settings.PROVIDER_RANDOM_SEED)
    for name in registry.names():
        provider = registry.get(name)
        provider.behaviour.setup_latency_range = (0.0, 0.0)
        provider.behaviour.failure_rate = 0.0
        provider.behaviour.hang_rate = 0.0
        provider.behaviour.ring_duration = 3600.0
        provider.behaviour.avg_talk_time = 3600.0

    dialer = ProgressiveDialer(
        snapshot_builder=MetricsSnapshotBuilder(agents, calls, health, settings),
        safety_controller=SafetyController(
            agent_repository=agents,
            call_repository=calls,
            decision_repository=decisions,
            health_manager=health,
            availability_tracker=AgentAvailabilityTracker(settings),
            settings=settings,
        ),
        call_allocator=CallAllocator(
            reservation_service=ReservationService(agents, borrowers, settings, counters),
            call_repository=calls,
            agent_repository=agents,
            borrower_repository=borrowers,
            provider_registry=registry,
            health_manager=health,
            retry_service=retries,
            settings=settings,
            registry=counters,
        ),
        decision_repository=decisions,
        settings=settings,
    )

    timer = Timer()
    started = asyncio.get_running_loop().time()
    for index in range(TICKS):
        await timer.measure(dialer.tick(campaign, f"worker-{index}"))
    duration = asyncio.get_running_loop().time() - started

    await registry.shutdown()

    allocated = await database["calls"].count_documents({})
    return summarise(
        name="dialer_tick",
        scale=scale,
        latencies_ms=timer.latencies_ms,
        duration_seconds=duration,
        extra={
            "ticks": TICKS,
            "calls_allocated": allocated,
            "tick_budget_ms": settings.DIALER_TICK_SECONDS * 1000,
        },
    )
