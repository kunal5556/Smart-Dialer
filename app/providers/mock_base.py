import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.providers.base import (
    EventCallback,
    OriginateRequest,
    OriginateResult,
    ProviderCallStatus,
    ProviderEvent,
    ProviderHealthSnapshot,
)
from app.providers.errors import ProviderRejected, ProviderTimeout

FIRST_EVENT_MIN_DELAY_SECONDS = 0.01


@dataclass
class MockBehaviour:
    setup_latency_range: tuple[float, float]
    failure_rate: float
    hang_rate: float
    answer_rate: float
    avg_talk_time: float
    ring_duration: float
    duplicate_rate: float
    out_of_order_rate: float


class MockProviderBase:
    def __init__(
        self,
        name: str,
        on_event: EventCallback,
        behaviour: MockBehaviour,
        seed: int,
    ) -> None:
        self.name = name
        self.behaviour = behaviour
        self._on_event = on_event
        self._rng = random.Random(seed)
        self._call_counter = 0
        self._active_calls: dict[str, asyncio.Task] = {}
        self._finished_calls: dict[str, ProviderCallStatus] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        self._outage_until: float | None = None
        self._originate_attempts = 0
        self._originate_accepted = 0
        self._originate_rejected = 0
        self._originate_timed_out = 0

    async def originate_call(self, request: OriginateRequest) -> OriginateResult:
        if not request.phone_number.strip():
            raise ProviderRejected(self.name, "phone_number is required")

        self._originate_attempts += 1
        started_at = time.monotonic()

        if self._is_in_outage() or self._rng.random() < self.behaviour.hang_rate:
            await self._hang_until_timeout(request)

        latency_seconds = self._rng.uniform(*self.behaviour.setup_latency_range)
        if latency_seconds >= request.timeout_seconds:
            await self._hang_until_timeout(request)

        await asyncio.sleep(latency_seconds)
        latency_ms = int((time.monotonic() - started_at) * 1000)

        if self._rng.random() < self.behaviour.failure_rate:
            self._originate_rejected += 1
            return OriginateResult(
                accepted=False,
                latency_ms=latency_ms,
                error_code="carrier_rejected",
            )

        provider_call_id = self._next_provider_call_id()
        events, final_status = self._build_event_script(provider_call_id)
        self._originate_accepted += 1

        task = asyncio.create_task(self._deliver_events(provider_call_id, events, final_status))
        self._active_calls[provider_call_id] = task
        self._track(task)

        return OriginateResult(
            accepted=True,
            latency_ms=latency_ms,
            provider_call_id=provider_call_id,
        )

    async def hangup_call(self, provider_call_id: str) -> None:
        task = self._active_calls.pop(provider_call_id, None)
        if task is None:
            return
        task.cancel()
        self._finished_calls[provider_call_id] = ProviderCallStatus.COMPLETED

    async def get_call_status(self, provider_call_id: str) -> ProviderCallStatus:
        if provider_call_id in self._active_calls:
            return ProviderCallStatus.ACTIVE
        return self._finished_calls.get(provider_call_id, ProviderCallStatus.UNKNOWN)

    def health_snapshot(self) -> ProviderHealthSnapshot:
        return ProviderHealthSnapshot(
            provider_name=self.name,
            in_outage=self._is_in_outage(),
            originate_attempts=self._originate_attempts,
            originate_accepted=self._originate_accepted,
            originate_rejected=self._originate_rejected,
            originate_timed_out=self._originate_timed_out,
        )

    async def shutdown(self) -> None:
        pending = list(self._pending_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending_tasks.clear()
        self._active_calls.clear()

    def _is_in_outage(self) -> bool:
        return self._outage_until is not None and time.monotonic() < self._outage_until

    async def _hang_until_timeout(self, request: OriginateRequest) -> None:
        await asyncio.sleep(request.timeout_seconds)
        self._originate_timed_out += 1
        raise ProviderTimeout(self.name, request.call_id, request.timeout_seconds)

    def _next_provider_call_id(self) -> str:
        self._call_counter += 1
        return f"{self.name}-call-{self._call_counter}"

    def _build_event_script(
        self,
        provider_call_id: str,
    ) -> tuple[list[tuple[float, ProviderEvent]], ProviderCallStatus]:
        answered = self._rng.random() < self.behaviour.answer_rate
        steps: list[tuple[float, str, dict[str, str]]] = [
            (self.behaviour.ring_duration, "RINGING", {})
        ]

        if answered:
            steps.append((self.behaviour.ring_duration, "ANSWERED", {}))
            steps.append((0.0, "CONNECTED", {}))
            talk_time = max(
                0.0,
                self._rng.gauss(self.behaviour.avg_talk_time, self.behaviour.avg_talk_time * 0.2),
            )
            steps.append((talk_time, "COMPLETED", {}))
            final_status = ProviderCallStatus.COMPLETED
        else:
            steps.append((self.behaviour.ring_duration, "FAILED", {"reason": "no_answer"}))
            final_status = ProviderCallStatus.FAILED

        scheduled = [
            (
                delay,
                ProviderEvent(
                    provider_name=self.name,
                    provider_event_id=f"{provider_call_id}-{index}",
                    provider_call_id=provider_call_id,
                    event_type=event_type,
                    provider_timestamp=datetime.now(timezone.utc),
                    payload=payload,
                ),
            )
            for index, (delay, event_type, payload) in enumerate(steps)
        ]

        self._apply_out_of_order(scheduled)
        self._apply_duplication(scheduled)
        return scheduled, final_status

    def _apply_out_of_order(self, scheduled: list[tuple[float, ProviderEvent]]) -> None:
        if len(scheduled) < 2:
            return
        if self._rng.random() >= self.behaviour.out_of_order_rate:
            return
        index = self._rng.randrange(len(scheduled) - 1)
        scheduled[index], scheduled[index + 1] = scheduled[index + 1], scheduled[index]

    def _apply_duplication(self, scheduled: list[tuple[float, ProviderEvent]]) -> None:
        if not scheduled:
            return
        if self._rng.random() >= self.behaviour.duplicate_rate:
            return
        index = self._rng.randrange(len(scheduled))
        delay, event = scheduled[index]
        scheduled.insert(index + 1, (min(delay, 0.001), event))

    async def _deliver_events(
        self,
        provider_call_id: str,
        scheduled: list[tuple[float, ProviderEvent]],
        final_status: ProviderCallStatus,
    ) -> None:
        try:
            await asyncio.sleep(FIRST_EVENT_MIN_DELAY_SECONDS)
            for delay, event in scheduled:
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._on_event(event)
            self._finished_calls[provider_call_id] = final_status
        finally:
            self._active_calls.pop(provider_call_id, None)

    def _track(self, task: asyncio.Task) -> None:
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
