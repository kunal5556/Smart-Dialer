from dataclasses import dataclass, field

from app.models.enums import DialingMode


@dataclass(frozen=True)
class AvailabilityChange:
    at_second: float
    agents_offline: int


@dataclass(frozen=True)
class ConditionChange:
    at_second: float
    answer_rate: float | None = None
    avg_talk_time_seconds: float | None = None


@dataclass(frozen=True)
class SimulationConfig:
    name: str
    agents: int = 20
    borrowers: int = 500
    answer_rate: float = 0.3
    avg_talk_time_seconds: float = 120.0
    talk_time_variance: float = 0.2
    setup_latency_seconds: tuple[float, float] = (0.05, 0.15)
    ring_duration_seconds: float = 3.0
    provider_name: str = "mock_a"
    provider_failure_rate: float = 0.02
    worker_count: int = 1
    dialing_mode: DialingMode = DialingMode.PROGRESSIVE
    duration_seconds: float = 60.0
    time_scale: float = 30.0
    seed: int = 1234
    baseline_answer_rate: float = 0.3
    availability_schedule: tuple[AvailabilityChange, ...] = field(default_factory=tuple)
    condition_schedule: tuple[ConditionChange, ...] = field(default_factory=tuple)

    def scaled(self, seconds: float) -> float:
        return seconds / self.time_scale

    @property
    def wall_clock_seconds(self) -> float:
        return self.scaled(self.duration_seconds)
