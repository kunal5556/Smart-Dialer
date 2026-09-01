import time

from app.providers.base import EventCallback
from app.providers.mock_base import MockBehaviour, MockProviderBase

PROVIDER_B_NAME = "mock_b"


def default_behaviour() -> MockBehaviour:
    return MockBehaviour(
        setup_latency_range=(0.8, 2.5),
        failure_rate=0.15,
        hang_rate=0.08,
        answer_rate=0.3,
        avg_talk_time=120.0,
        ring_duration=3.0,
        duplicate_rate=0.10,
        out_of_order_rate=0.10,
    )


class MockProviderB(MockProviderBase):
    def __init__(self, on_event: EventCallback, seed: int) -> None:
        super().__init__(
            name=PROVIDER_B_NAME,
            on_event=on_event,
            behaviour=default_behaviour(),
            seed=seed,
        )

    def force_outage(self, seconds: float) -> None:
        self._outage_until = time.monotonic() + seconds

    def clear_outage(self) -> None:
        self._outage_until = None
