import logging
from dataclasses import dataclass

from app.logging_config import log_event
from app.providers.mock_b import MockProviderB
from app.providers.registry import ProviderRegistry
from app.repositories.event_repo import EventRepository
from app.services.event_processor import EventProcessor
from app.simulation.agent_simulator import AgentSimulator

logger = logging.getLogger(__name__)

FAULT_PROVIDER_LATENCY_SPIKE = "provider_latency_spike"
FAULT_PROVIDER_OUTAGE = "provider_outage"
FAULT_DUPLICATE_EVENTS = "duplicate_event_burst"
FAULT_OUT_OF_ORDER_EVENTS = "out_of_order_burst"
FAULT_AGENT_AVAILABILITY_DROP = "agent_availability_drop"

AVAILABLE_FAULTS = (
    FAULT_PROVIDER_LATENCY_SPIKE,
    FAULT_PROVIDER_OUTAGE,
    FAULT_DUPLICATE_EVENTS,
    FAULT_OUT_OF_ORDER_EVENTS,
    FAULT_AGENT_AVAILABILITY_DROP,
)


@dataclass(frozen=True)
class FaultResult:
    fault: str
    detail: str
    affected: int


class FaultInjector:
    def __init__(
        self,
        provider_registry: ProviderRegistry,
        event_repository: EventRepository,
        event_processor: EventProcessor,
    ) -> None:
        self._providers = provider_registry
        self._events = event_repository
        self._processor = event_processor

    def provider_latency_spike(self, provider_name: str, multiplier: float = 20.0) -> FaultResult:
        provider = self._providers.get(provider_name)
        low, high = provider.behaviour.setup_latency_range
        provider.behaviour.setup_latency_range = (low * multiplier, high * multiplier)
        return FaultResult(
            fault=FAULT_PROVIDER_LATENCY_SPIKE,
            detail=f"setup latency multiplied by {multiplier:g}",
            affected=1,
        )

    def provider_outage(self, provider_name: str, seconds: float) -> FaultResult:
        provider = self._providers.get(provider_name)
        if not isinstance(provider, MockProviderB):
            return FaultResult(
                fault=FAULT_PROVIDER_OUTAGE,
                detail=f"{provider_name} does not support forced outages",
                affected=0,
            )
        provider.force_outage(seconds)
        log_event(
            logger,
            logging.WARNING,
            "fault_provider_outage",
            f"Forced a {seconds:g}s outage on {provider_name}",
        )
        return FaultResult(
            fault=FAULT_PROVIDER_OUTAGE,
            detail=f"{provider_name} will time out for {seconds:g}s",
            affected=1,
        )

    async def duplicate_event_burst(self, provider_call_id: str | None = None) -> FaultResult:
        events = await self._replayable_events(provider_call_id)
        for event in events:
            await self._processor.process_event(event)
        return FaultResult(
            fault=FAULT_DUPLICATE_EVENTS,
            detail=f"replayed {len(events)} genuine provider events",
            affected=len(events),
        )

    async def out_of_order_burst(self, provider_call_id: str | None = None) -> FaultResult:
        events = await self._replayable_events(provider_call_id)
        for event in reversed(events):
            await self._processor.process_event(event)
        return FaultResult(
            fault=FAULT_OUT_OF_ORDER_EVENTS,
            detail=f"replayed {len(events)} events in reverse order",
            affected=len(events),
        )

    async def agent_availability_drop(
        self,
        agent_simulator: AgentSimulator,
        count: int,
    ) -> FaultResult:
        removed = await agent_simulator.take_agents_offline(count)
        return FaultResult(
            fault=FAULT_AGENT_AVAILABILITY_DROP,
            detail=f"{removed} agents taken offline",
            affected=removed,
        )

    async def _replayable_events(self, provider_call_id: str | None) -> list:
        if provider_call_id is None:
            return await self._events.find_latest(limit=20)
        return await self._events.find_provider_events_for_call(provider_call_id)
