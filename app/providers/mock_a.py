from app.providers.base import EventCallback
from app.providers.mock_base import MockBehaviour, MockProviderBase

PROVIDER_A_NAME = "mock_a"


def default_behaviour() -> MockBehaviour:
    return MockBehaviour(
        setup_latency_range=(0.15, 0.25),
        failure_rate=0.02,
        hang_rate=0.0,
        answer_rate=0.3,
        avg_talk_time=120.0,
        ring_duration=3.0,
        duplicate_rate=0.0,
        out_of_order_rate=0.0,
    )


class MockProviderA(MockProviderBase):
    def __init__(self, on_event: EventCallback, seed: int) -> None:
        super().__init__(
            name=PROVIDER_A_NAME,
            on_event=on_event,
            behaviour=default_behaviour(),
            seed=seed,
        )
