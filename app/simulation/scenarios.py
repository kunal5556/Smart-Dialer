from app.models.enums import DialingMode
from app.simulation.config import AvailabilityChange, ConditionChange, SimulationConfig

SCENARIO_A = "A"
SCENARIO_B = "B"
SCENARIO_C = "C"
SCENARIO_D = "D"
SCENARIO_FAULTS = "faults"

SCENARIO_NAMES = (SCENARIO_A, SCENARIO_B, SCENARIO_C, SCENARIO_D, SCENARIO_FAULTS)

BASE_SETTINGS: dict[str, dict] = {
    SCENARIO_A: {"answer_rate": 0.20, "avg_talk_time_seconds": 120.0},
    SCENARIO_B: {"answer_rate": 0.50, "avg_talk_time_seconds": 90.0},
    SCENARIO_C: {"answer_rate": 0.70, "avg_talk_time_seconds": 180.0},
    SCENARIO_D: {"answer_rate": 0.70, "avg_talk_time_seconds": 150.0},
    SCENARIO_FAULTS: {"answer_rate": 0.30, "avg_talk_time_seconds": 90.0},
}


def build_scenario(
    scenario: str,
    mode: DialingMode,
    agents: int = 20,
    borrowers: int = 500,
    duration_seconds: float = 60.0,
    seed: int = 1234,
    time_scale: float = 30.0,
) -> SimulationConfig:
    if scenario not in SCENARIO_NAMES:
        raise ValueError(f"Unknown scenario {scenario}; expected one of {SCENARIO_NAMES}")

    base = BASE_SETTINGS[scenario]
    config = SimulationConfig(
        name=scenario,
        agents=agents,
        borrowers=borrowers,
        answer_rate=base["answer_rate"],
        baseline_answer_rate=base["answer_rate"],
        avg_talk_time_seconds=base["avg_talk_time_seconds"],
        dialing_mode=mode,
        duration_seconds=duration_seconds,
        seed=seed,
        time_scale=time_scale,
    )

    if scenario == SCENARIO_D:
        return _with_changing_conditions(config)
    if scenario == SCENARIO_FAULTS:
        return _with_faults(config, agents)
    return config


def _with_changing_conditions(config: SimulationConfig) -> SimulationConfig:
    from dataclasses import replace

    return replace(
        config,
        condition_schedule=(
            ConditionChange(
                at_second=config.duration_seconds * 0.4,
                answer_rate=0.30,
                avg_talk_time_seconds=90.0,
            ),
            ConditionChange(
                at_second=config.duration_seconds * 0.7,
                answer_rate=0.10,
                avg_talk_time_seconds=210.0,
            ),
        ),
    )


def _with_faults(config: SimulationConfig, agents: int) -> SimulationConfig:
    from dataclasses import replace

    return replace(
        config,
        provider_name="mock_b",
        provider_failure_rate=0.15,
        availability_schedule=(
            AvailabilityChange(
                at_second=config.duration_seconds * 0.5,
                agents_offline=max(1, int(agents * 0.4)),
            ),
        ),
    )
