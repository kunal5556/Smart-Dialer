from app.providers.mock_base import MockProviderBase
from app.simulation.config import SimulationConfig


def configure_provider(provider: MockProviderBase, config: SimulationConfig) -> None:
    provider.behaviour.answer_rate = config.answer_rate
    provider.behaviour.failure_rate = config.provider_failure_rate
    provider.behaviour.avg_talk_time = config.scaled(config.avg_talk_time_seconds)
    provider.behaviour.ring_duration = config.scaled(config.ring_duration_seconds)
    provider.behaviour.setup_latency_range = (
        config.scaled(config.setup_latency_seconds[0]),
        config.scaled(config.setup_latency_seconds[1]),
    )


def apply_answer_rate(provider: MockProviderBase, answer_rate: float) -> None:
    provider.behaviour.answer_rate = answer_rate


def apply_talk_time(provider: MockProviderBase, config: SimulationConfig, seconds: float) -> None:
    provider.behaviour.avg_talk_time = config.scaled(seconds)
