import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import Settings
from app.logging_config import log_event
from app.models.base import new_id, utc_now
from app.simulation.config import SimulationConfig
from app.simulation.engine import SimulationEngine, SimulationReport

logger = logging.getLogger(__name__)

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"


@dataclass
class SimulationRun:
    id: str
    config: SimulationConfig
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    report: SimulationReport | None = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


class SimulationRunner:
    def __init__(self, database: AsyncIOMotorDatabase, settings: Settings) -> None:
        self._engine = SimulationEngine(database, settings)
        self._runs: dict[str, SimulationRun] = {}

    def start(self, config: SimulationConfig) -> SimulationRun:
        if self.active_run() is not None:
            raise RuntimeError("A simulation is already running")

        run = SimulationRun(
            id=new_id(),
            config=config,
            status=STATUS_RUNNING,
            started_at=utc_now(),
        )
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._execute(run))
        return run

    def active_run(self) -> SimulationRun | None:
        for run in self._runs.values():
            if run.status == STATUS_RUNNING:
                return run
        return None

    def get(self, run_id: str) -> SimulationRun | None:
        return self._runs.get(run_id)

    def history(self) -> list[SimulationRun]:
        return sorted(self._runs.values(), key=lambda run: run.started_at, reverse=True)

    async def shutdown(self) -> None:
        for run in self._runs.values():
            if run.task is not None and not run.task.done():
                run.task.cancel()
                try:
                    await run.task
                except asyncio.CancelledError:
                    pass

    async def _execute(self, run: SimulationRun) -> None:
        try:
            run.report = await self._engine.run(run.config)
            run.status = STATUS_COMPLETED if run.report.passed else STATUS_FAILED
            run.error = run.report.error
        except asyncio.CancelledError:
            run.status = STATUS_FAILED
            run.error = "cancelled"
            raise
        except Exception as error:
            run.status = STATUS_FAILED
            run.error = f"{type(error).__name__}: {error}"
            log_event(
                logger,
                logging.ERROR,
                "simulation_run_failed",
                f"Simulation {run.id} failed: {error}",
            )
        finally:
            run.finished_at = utc_now()
