import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from app.config import Settings
from app.logging_config import log_event
from app.models.base import utc_now
from app.models.enums import ProviderHealthStatus

logger = logging.getLogger(__name__)

HEALTH_FACTORS: dict[ProviderHealthStatus, float] = {
    ProviderHealthStatus.HEALTHY: 1.0,
    ProviderHealthStatus.DEGRADED: 0.5,
    ProviderHealthStatus.UNHEALTHY: 0.0,
}


@dataclass(frozen=True)
class OriginateSample:
    recorded_at: float
    success: bool
    timed_out: bool
    latency_ms: int


@dataclass(frozen=True)
class ProviderHealth:
    provider_name: str
    status: ProviderHealthStatus
    request_count: int
    success_rate: float
    failure_rate: float
    timeout_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    consecutive_failures: int
    events_received: int
    low_confidence: bool
    computed_at: datetime


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


class ProviderHealthManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._samples: dict[str, deque[OriginateSample]] = {}
        self._events_received: dict[str, int] = {}
        self._last_status: dict[str, ProviderHealthStatus] = {}

    def record_originate(
        self,
        provider_name: str,
        success: bool,
        latency_ms: int,
        timed_out: bool = False,
    ) -> None:
        window = self._samples.setdefault(provider_name, deque())
        window.append(
            OriginateSample(
                recorded_at=time.monotonic(),
                success=success,
                timed_out=timed_out,
                latency_ms=latency_ms,
            )
        )
        self._trim(window)

    def record_event_received(self, provider_name: str) -> None:
        self._events_received[provider_name] = self._events_received.get(provider_name, 0) + 1

    def get_health(self, provider_name: str) -> ProviderHealth:
        try:
            return self._compute_health(provider_name)
        except Exception as error:
            log_event(
                logger,
                logging.ERROR,
                "provider_health_computation_failed",
                f"Falling back to DEGRADED for {provider_name}: {error}",
            )
            return ProviderHealth(
                provider_name=provider_name,
                status=ProviderHealthStatus.DEGRADED,
                request_count=0,
                success_rate=0.0,
                failure_rate=0.0,
                timeout_rate=0.0,
                p50_latency_ms=0.0,
                p95_latency_ms=0.0,
                consecutive_failures=0,
                events_received=self._events_received.get(provider_name, 0),
                low_confidence=True,
                computed_at=utc_now(),
            )

    def should_allow_retry(self, provider_name: str) -> bool:
        return self.get_health(provider_name).status is not ProviderHealthStatus.UNHEALTHY

    def health_factor(self, provider_name: str) -> float:
        return HEALTH_FACTORS[self.get_health(provider_name).status]

    def _compute_health(self, provider_name: str) -> ProviderHealth:
        window = self._samples.setdefault(provider_name, deque())
        self._trim(window)
        samples = list(window)

        request_count = len(samples)
        low_confidence = request_count < self._settings.HEALTH_MIN_SAMPLES

        if request_count == 0:
            success_rate = 1.0
            failure_rate = 0.0
            timeout_rate = 0.0
        else:
            successes = sum(1 for sample in samples if sample.success)
            timeouts = sum(1 for sample in samples if sample.timed_out)
            success_rate = successes / request_count
            failure_rate = 1.0 - success_rate
            timeout_rate = timeouts / request_count

        latencies = [float(sample.latency_ms) for sample in samples]
        consecutive_failures = self._count_trailing_failures(samples)
        status = self._derive_status(
            request_count=request_count,
            failure_rate=failure_rate,
            timeout_rate=timeout_rate,
            consecutive_failures=consecutive_failures,
            p95_latency_ms=percentile(latencies, 0.95),
            low_confidence=low_confidence,
        )
        self._log_status_change(provider_name, status)

        return ProviderHealth(
            provider_name=provider_name,
            status=status,
            request_count=request_count,
            success_rate=success_rate,
            failure_rate=failure_rate,
            timeout_rate=timeout_rate,
            p50_latency_ms=percentile(latencies, 0.5),
            p95_latency_ms=percentile(latencies, 0.95),
            consecutive_failures=consecutive_failures,
            events_received=self._events_received.get(provider_name, 0),
            low_confidence=low_confidence,
            computed_at=utc_now(),
        )

    def _derive_status(
        self,
        request_count: int,
        failure_rate: float,
        timeout_rate: float,
        consecutive_failures: int,
        p95_latency_ms: float,
        low_confidence: bool,
    ) -> ProviderHealthStatus:
        if consecutive_failures >= self._settings.UNHEALTHY_CONSECUTIVE_FAILURES:
            return ProviderHealthStatus.UNHEALTHY
        if not low_confidence and timeout_rate > self._settings.UNHEALTHY_TIMEOUT_RATE:
            return ProviderHealthStatus.UNHEALTHY
        if low_confidence:
            return ProviderHealthStatus.HEALTHY
        if failure_rate > self._settings.DEGRADED_FAILURE_RATE:
            return ProviderHealthStatus.DEGRADED
        if p95_latency_ms > self._settings.DEGRADED_LATENCY_MS:
            return ProviderHealthStatus.DEGRADED
        return ProviderHealthStatus.HEALTHY

    def _count_trailing_failures(self, samples: list[OriginateSample]) -> int:
        failures = 0
        for sample in reversed(samples):
            if sample.success:
                break
            failures += 1
        return failures

    def _trim(self, window: deque[OriginateSample]) -> None:
        cutoff = time.monotonic() - self._settings.HEALTH_WINDOW_SECONDS
        while window and window[0].recorded_at < cutoff:
            window.popleft()

    def _log_status_change(self, provider_name: str, status: ProviderHealthStatus) -> None:
        if self._last_status.get(provider_name) is status:
            return
        previous = self._last_status.get(provider_name)
        self._last_status[provider_name] = status
        log_event(
            logger,
            logging.WARNING if status is not ProviderHealthStatus.HEALTHY else logging.INFO,
            "provider_health_changed",
            f"Provider {provider_name} health moved from "
            f"{previous.value if previous else 'UNKNOWN'} to {status.value}",
        )
