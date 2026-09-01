import json
import pathlib
from dataclasses import asdict
from datetime import datetime, timezone

from app.simulation.engine import SimulationReport

RESULTS_DIRECTORY = pathlib.Path("simulation_results")


def report_to_dict(report: SimulationReport) -> dict:
    metrics = report.metrics
    return {
        "scenario": report.config.name,
        "mode": report.config.dialing_mode.value,
        "passed": report.passed,
        "error": report.error,
        "config": {
            "agents": report.config.agents,
            "borrowers": report.config.borrowers,
            "answer_rate": report.config.answer_rate,
            "avg_talk_time_seconds": report.config.avg_talk_time_seconds,
            "provider_name": report.config.provider_name,
            "worker_count": report.config.worker_count,
            "duration_seconds": report.config.duration_seconds,
            "time_scale": report.config.time_scale,
            "seed": report.config.seed,
        },
        "metrics": _metrics_to_dict(metrics),
        "violations": [asdict(violation) for violation in report.violations],
        "faults": [asdict(fault) for fault in report.faults],
    }


def _metrics_to_dict(metrics) -> dict | None:
    if metrics is None:
        return None
    document = metrics.to_document()
    document["collected_at"] = document["collected_at"].isoformat()
    return document


def write_report(report: SimulationReport, directory: pathlib.Path | None = None) -> pathlib.Path:
    target = directory or RESULTS_DIRECTORY
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = f"{report.config.name}_{report.config.dialing_mode.value.lower()}_{stamp}.json"
    path = target / filename
    path.write_text(json.dumps(report_to_dict(report), indent=2), encoding="utf-8")
    return path


def comparison_table(reports: list[SimulationReport]) -> str:
    header = (
        f"{'scenario':<10}{'mode':<13}{'util':>8}{'connected':>11}"
        f"{'completed':>11}{'reduced':>9}{'rejected':>10}{'fallback':>10}{'invariants':>12}"
    )
    lines = [header, "-" * len(header)]
    for report in reports:
        metrics = report.metrics
        utilization = "n/a"
        connected = completed = reduced = rejected = fallback = 0
        if metrics is not None:
            if metrics.talk_utilization is not None:
                utilization = f"{metrics.talk_utilization:.1%}"
            connected = metrics.calls_connected
            completed = metrics.calls_completed
            reduced = metrics.safety_verdicts.get("REDUCED", 0)
            rejected = metrics.safety_verdicts.get("REJECTED", 0)
            fallback = metrics.progressive_fallbacks
        lines.append(
            f"{report.config.name:<10}{report.config.dialing_mode.value:<13}"
            f"{utilization:>8}{connected:>11}{completed:>11}"
            f"{reduced:>9}{rejected:>10}{fallback:>10}"
            f"{'PASS' if report.passed else 'FAIL':>12}"
        )
    return "\n".join(lines)
