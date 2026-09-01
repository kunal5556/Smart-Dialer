from collections import Counter

COUNTER_RESERVATION_CONTENTION = "reservation_contention"
COUNTER_RETRY_SUPPRESSED = "retry_suppressed"
COUNTER_RETRY_ATTEMPTS = "retry_attempts"
COUNTER_PROVIDER_FAILURES = "provider_failures"
COUNTER_EVENTS_IGNORED = "events_ignored"


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        self._counters[name] += amount

    def value(self, name: str) -> int:
        return self._counters[name]

    def snapshot(self) -> dict[str, int]:
        return dict(self._counters)

    def reset(self) -> None:
        self._counters.clear()
