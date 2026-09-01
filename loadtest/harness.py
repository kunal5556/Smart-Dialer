import json
import pathlib
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import Settings, load_settings
from app.db_indexes import ensure_indexes
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.enums import AgentState, CampaignStatus

LOADTEST_DB_NAME = "smartdialer_loadtest"
RESULTS_DIRECTORY = pathlib.Path("loadtest_results")
INSERT_BATCH_SIZE = 2000
DEFAULT_POOL_SIZE = 100


@dataclass
class LoadTestResult:
    name: str
    scale: int
    operations: int
    duration_seconds: float
    ops_per_second: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    pool_size: int
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarise(
    name: str,
    scale: int,
    latencies_ms: list[float],
    duration_seconds: float,
    pool_size: int = DEFAULT_POOL_SIZE,
    extra: dict | None = None,
) -> LoadTestResult:
    operations = len(latencies_ms)
    return LoadTestResult(
        name=name,
        scale=scale,
        operations=operations,
        duration_seconds=round(duration_seconds, 4),
        ops_per_second=round(operations / duration_seconds, 1) if duration_seconds else 0.0,
        p50_ms=round(percentile(latencies_ms, 0.50), 3),
        p95_ms=round(percentile(latencies_ms, 0.95), 3),
        p99_ms=round(percentile(latencies_ms, 0.99), 3),
        pool_size=pool_size,
        extra=extra or {},
    )


class Timer:
    def __init__(self) -> None:
        self.latencies_ms: list[float] = []

    async def measure(self, awaitable):
        started = time.perf_counter()
        result = await awaitable
        self.latencies_ms.append((time.perf_counter() - started) * 1000)
        return result

    @property
    def mean_ms(self) -> float:
        return statistics.fmean(self.latencies_ms) if self.latencies_ms else 0.0


def build_client(settings: Settings) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(
        settings.MONGODB_URI,
        maxPoolSize=DEFAULT_POOL_SIZE,
        uuidRepresentation="standard",
        tz_aware=True,
    )


async def fresh_database() -> tuple[AsyncIOMotorClient, AsyncIOMotorDatabase, Settings]:
    settings = load_settings()
    client = build_client(settings)
    await client.drop_database(LOADTEST_DB_NAME)
    database = client[LOADTEST_DB_NAME]
    await ensure_indexes(database)
    return client, database, settings


async def seed_campaign(
    database: AsyncIOMotorDatabase,
    agents: int,
    borrowers: int,
    agent_state: AgentState = AgentState.AVAILABLE,
) -> Campaign:
    for collection in ("campaigns", "agents", "borrowers", "calls", "provider_events"):
        await database[collection].delete_many({})

    campaign = Campaign(
        name=f"Load test {agents} agents",
        status=CampaignStatus.RUNNING,
        max_concurrent_calls=max(agents * 2, 100),
    )
    await database["campaigns"].insert_one(campaign.to_mongo())

    await _insert_in_batches(
        database["agents"],
        (
            Agent(
                campaign_id=campaign.id,
                name=f"Agent {number:06d}",
                state=agent_state,
            ).to_mongo()
            for number in range(agents)
        ),
    )
    await _insert_in_batches(
        database["borrowers"],
        (
            Borrower(
                campaign_id=campaign.id,
                name=f"Borrower {number:06d}",
                phone_number=f"+1555{number:07d}",
            ).to_mongo()
            for number in range(borrowers)
        ),
    )
    return campaign


async def _insert_in_batches(collection, documents) -> None:
    batch: list[dict] = []
    for document in documents:
        batch.append(document)
        if len(batch) >= INSERT_BATCH_SIZE:
            await collection.insert_many(batch)
            batch = []
    if batch:
        await collection.insert_many(batch)


def write_results(results: list[LoadTestResult], directory: pathlib.Path | None = None) -> pathlib.Path:
    target = directory or RESULTS_DIRECTORY
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = target / f"loadtest_{stamp}.json"
    path.write_text(
        json.dumps([result.to_dict() for result in results], indent=2), encoding="utf-8"
    )
    return path


def results_table(results: list[LoadTestResult]) -> str:
    header = (
        f"{'measurement':<26}{'scale':>8}{'ops':>8}{'ops/sec':>11}"
        f"{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        lines.append(
            f"{result.name:<26}{result.scale:>8}{result.operations:>8}"
            f"{result.ops_per_second:>11}{result.p50_ms:>9}{result.p95_ms:>9}{result.p99_ms:>9}"
        )
    return "\n".join(lines)
