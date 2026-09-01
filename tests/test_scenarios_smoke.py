import pytest

from app.models.enums import DialingMode
from app.simulation.engine import SimulationEngine
from app.simulation.report import comparison_table, report_to_dict, write_report
from app.simulation.scenarios import (
    SCENARIO_A,
    SCENARIO_B,
    SCENARIO_C,
    SCENARIO_D,
    SCENARIO_FAULTS,
    SCENARIO_NAMES,
    build_scenario,
)

SMOKE_AGENTS = 4
SMOKE_BORROWERS = 60
SMOKE_DURATION_SECONDS = 120.0
SMOKE_TIME_SCALE = 240.0


@pytest.fixture
def engine(test_database, test_settings) -> SimulationEngine:
    return SimulationEngine(test_database, test_settings)


def smoke_config(scenario: str, mode: DialingMode):
    return build_scenario(
        scenario=scenario,
        mode=mode,
        agents=SMOKE_AGENTS,
        borrowers=SMOKE_BORROWERS,
        duration_seconds=SMOKE_DURATION_SECONDS,
        seed=4321,
        time_scale=SMOKE_TIME_SCALE,
    )


@pytest.mark.parametrize("scenario", [SCENARIO_A, SCENARIO_B, SCENARIO_C, SCENARIO_D])
@pytest.mark.parametrize("mode", [DialingMode.PROGRESSIVE, DialingMode.PREDICTIVE])
async def test_scenario_runs_without_invariant_violations(engine, scenario, mode):
    report = await engine.run(smoke_config(scenario, mode))

    assert report.error is None
    assert report.violations == []
    assert report.passed is True
    assert report.metrics is not None
    assert report.metrics.dialing_mode == mode.value


async def test_fault_scenario_survives_a_hostile_provider(engine):
    report = await engine.run(smoke_config(SCENARIO_FAULTS, DialingMode.PREDICTIVE))

    assert report.error is None
    assert report.violations == []
    assert report.config.provider_name == "mock_b"


def test_every_documented_scenario_can_be_built():
    for scenario in SCENARIO_NAMES:
        config = build_scenario(scenario, DialingMode.PROGRESSIVE)
        assert config.name == scenario
        assert config.answer_rate > 0


def test_scenario_parameters_match_the_roadmap():
    a = build_scenario(SCENARIO_A, DialingMode.PROGRESSIVE)
    b = build_scenario(SCENARIO_B, DialingMode.PROGRESSIVE)
    c = build_scenario(SCENARIO_C, DialingMode.PROGRESSIVE)

    assert (a.answer_rate, a.avg_talk_time_seconds) == (0.20, 120.0)
    assert (b.answer_rate, b.avg_talk_time_seconds) == (0.50, 90.0)
    assert (c.answer_rate, c.avg_talk_time_seconds) == (0.70, 180.0)


def test_scenario_d_changes_conditions_mid_run():
    config = build_scenario(SCENARIO_D, DialingMode.PREDICTIVE)

    assert len(config.condition_schedule) == 2
    assert config.condition_schedule[-1].answer_rate == 0.10
    assert config.condition_schedule[-1].avg_talk_time_seconds == 210.0


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError):
        build_scenario("Z", DialingMode.PROGRESSIVE)


async def test_the_same_seed_reproduces_the_same_dialing_decisions(engine):
    first = await engine.run(smoke_config(SCENARIO_B, DialingMode.PROGRESSIVE))
    second = await engine.run(smoke_config(SCENARIO_B, DialingMode.PROGRESSIVE))

    assert first.metrics.calls_initiated == second.metrics.calls_initiated
    assert abs(first.metrics.calls_completed - second.metrics.calls_completed) <= 2


async def test_predictive_asks_for_more_than_progressive_at_a_low_answer_rate(engine):
    progressive = await engine.run(smoke_config(SCENARIO_A, DialingMode.PROGRESSIVE))
    predictive = await engine.run(smoke_config(SCENARIO_A, DialingMode.PREDICTIVE))

    progressive_reduced = progressive.metrics.safety_verdicts["REDUCED"]
    predictive_reduced = predictive.metrics.safety_verdicts["REDUCED"]

    assert predictive_reduced > progressive_reduced


async def test_predictive_never_places_more_calls_than_safety_allows(engine):
    progressive = await engine.run(smoke_config(SCENARIO_A, DialingMode.PROGRESSIVE))
    predictive = await engine.run(smoke_config(SCENARIO_A, DialingMode.PREDICTIVE))

    assert predictive.violations == []
    assert progressive.violations == []
    assert predictive.metrics.calls_initiated <= progressive.metrics.calls_initiated * 2


async def test_report_is_written_and_readable(engine, tmp_path):
    report = await engine.run(smoke_config(SCENARIO_A, DialingMode.PROGRESSIVE))

    path = write_report(report, directory=tmp_path)
    payload = report_to_dict(report)

    assert path.exists()
    assert payload["scenario"] == SCENARIO_A
    assert payload["mode"] == DialingMode.PROGRESSIVE.value
    assert payload["metrics"]["campaign_id"]
    assert "PASS" in comparison_table([report])
