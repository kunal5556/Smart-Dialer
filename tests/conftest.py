import os
from dataclasses import dataclass
from typing import AsyncIterator

import pytest
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.config import Settings
from app.db_indexes import ensure_indexes
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.call import Call
from app.models.campaign import Campaign
from app.models.enums import AgentState, CallState
from app.repositories.agent_repo import AgentRepository
from app.repositories.base import (
    COLLECTION_AGENTS,
    COLLECTION_BORROWERS,
    COLLECTION_CALLS,
    COLLECTION_CAMPAIGNS,
    COLLECTION_PACING_DECISIONS,
    COLLECTION_PROVIDER_EVENTS,
    COLLECTION_METRICS_SAMPLES,
    COLLECTION_PROVIDER_HEALTH_SAMPLES,
    COLLECTION_SAFETY_DECISIONS,
)
from app.repositories.borrower_repo import BorrowerRepository
from app.repositories.call_repo import CallRepository
from app.repositories.event_repo import EventRepository
from app.repositories.health_repo import HealthRepository
from app.dialers.mode_router import ModeRouter
from app.dialers.predictive_dialer import PredictiveDialer
from app.dialers.progressive_dialer import ProgressiveDialer
from app.pacing.metrics_snapshot import MetricsSnapshotBuilder
from app.providers.base import ProviderEvent
from app.providers.registry import build_registry
from app.repositories.campaign_repo import CampaignRepository
from app.repositories.decision_repo import DecisionRepository
from app.safety.safety_controller import SafetyController
from app.services.call_allocator import CallAllocator
from app.services.event_processor import EventProcessor
from app.services.provider_health import ProviderHealthManager
from app.services.agent_availability import AgentAvailabilityTracker
from app.services.reservation_service import ReservationService
from app.services.retry_service import RetryService
from app.metrics.campaign_metrics import CampaignMetricsCollector
from app.metrics.collector import MetricsSampler
from app.metrics.registry import MetricsRegistry
from app.repositories.metrics_repo import MetricsRepository
from app.services.wrap_up_service import WrapUpService
from app.workers.recovery_worker import RecoveryWorker
from app.state_machines.agent_sm import TransitionActor

TEST_DB_NAME = "smartdialer_test"

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME", TEST_DB_NAME)
os.environ.setdefault("DIALER_ENABLED", "false")


@pytest.fixture(scope="module")
async def test_database() -> AsyncIterator[AsyncIOMotorDatabase]:
    client = AsyncIOMotorClient(
        os.environ["MONGODB_URI"],
        serverSelectionTimeoutMS=2000,
        uuidRepresentation="standard",
        tz_aware=True,
    )
    try:
        await client.admin.command("ping")
    except PyMongoError:
        client.close()
        pytest.skip("MongoDB is not reachable at MONGODB_URI")

    await client.drop_database(TEST_DB_NAME)
    database = client[TEST_DB_NAME]
    await ensure_indexes(database)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB_NAME)
        client.close()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(MONGODB_URI=os.environ["MONGODB_URI"])


@pytest.fixture
def agent_repository(test_database) -> AgentRepository:
    return AgentRepository(test_database)


@pytest.fixture
def borrower_repository(test_database) -> BorrowerRepository:
    return BorrowerRepository(test_database)


@pytest.fixture
async def clean_reservation_collections(test_database) -> AsyncIterator[None]:
    for collection_name in (COLLECTION_CAMPAIGNS, COLLECTION_AGENTS, COLLECTION_BORROWERS):
        await test_database[collection_name].delete_many({})
    yield
    for collection_name in (COLLECTION_CAMPAIGNS, COLLECTION_AGENTS, COLLECTION_BORROWERS):
        await test_database[collection_name].delete_many({})


async def insert_campaign(database: AsyncIOMotorDatabase, name: str = "Test Campaign") -> Campaign:
    campaign = Campaign(name=name)
    await database[COLLECTION_CAMPAIGNS].insert_one(campaign.to_mongo())
    return campaign


async def insert_agents(
    database: AsyncIOMotorDatabase,
    campaign_id: str,
    count: int,
    state=None,
) -> list[Agent]:
    agents = []
    for number in range(1, count + 1):
        agent = Agent(campaign_id=campaign_id, name=f"Agent {number:03d}")
        if state is not None:
            agent = agent.model_copy(update={"state": state})
        agents.append(agent)
    if agents:
        await database[COLLECTION_AGENTS].insert_many(agent.to_mongo() for agent in agents)
    return agents


async def insert_borrowers(
    database: AsyncIOMotorDatabase,
    campaign_id: str,
    count: int,
) -> list[Borrower]:
    borrowers = [
        Borrower(
            campaign_id=campaign_id,
            name=f"Borrower {number:04d}",
            phone_number=f"+1555{number:07d}",
        )
        for number in range(1, count + 1)
    ]
    if borrowers:
        await database[COLLECTION_BORROWERS].insert_many(
            borrower.to_mongo() for borrower in borrowers
        )
    return borrowers


@pytest.fixture
def call_repository(test_database) -> CallRepository:
    return CallRepository(test_database)


@pytest.fixture
def event_repository(test_database) -> EventRepository:
    return EventRepository(test_database)


@pytest.fixture
def event_processor(
    call_repository,
    event_repository,
    agent_repository,
    borrower_repository,
    retry_service,
    test_settings,
) -> EventProcessor:
    return EventProcessor(
        call_repository=call_repository,
        event_repository=event_repository,
        agent_repository=agent_repository,
        borrower_repository=borrower_repository,
        retry_service=retry_service,
        settings=test_settings,
    )


CALL_COLLECTIONS = (
    COLLECTION_CAMPAIGNS,
    COLLECTION_AGENTS,
    COLLECTION_BORROWERS,
    COLLECTION_CALLS,
    COLLECTION_PROVIDER_EVENTS,
    COLLECTION_METRICS_SAMPLES,
    COLLECTION_PROVIDER_HEALTH_SAMPLES,
    COLLECTION_METRICS_SAMPLES,
    COLLECTION_PACING_DECISIONS,
    COLLECTION_SAFETY_DECISIONS,
)


@pytest.fixture
async def clean_call_collections(test_database) -> AsyncIterator[None]:
    for collection_name in CALL_COLLECTIONS:
        await test_database[collection_name].delete_many({})
    yield
    for collection_name in CALL_COLLECTIONS:
        await test_database[collection_name].delete_many({})


@dataclass
class DialingCallContext:
    campaign: Campaign
    agent: Agent
    borrower: Borrower
    call: Call
    provider_call_id: str
    worker_id: str


async def prepare_dialing_call(
    database: AsyncIOMotorDatabase,
    call_repository: CallRepository,
    worker_id: str = "worker-1",
    provider_name: str = "mock_a",
    provider_call_id: str = "seeded-provider-call-1",
) -> DialingCallContext:
    agents = AgentRepository(database)
    borrowers = BorrowerRepository(database)

    campaign = await insert_campaign(database)
    [seeded_agent] = await insert_agents(database, campaign.id, 1, state=AgentState.AVAILABLE)
    [seeded_borrower] = await insert_borrowers(database, campaign.id, 1)

    agent = await agents.try_reserve_agent(
        campaign_id=campaign.id,
        agent_id=seeded_agent.id,
        worker_id=worker_id,
        ttl_seconds=30,
    )
    borrower = await borrowers.try_reserve_borrower(
        campaign_id=campaign.id,
        borrower_id=seeded_borrower.id,
        worker_id=worker_id,
        ttl_seconds=30,
    )

    call = await call_repository.create_call(
        campaign_id=campaign.id,
        agent_id=agent.id,
        borrower_id=borrower.id,
        provider_name=provider_name,
        worker_id=worker_id,
    )
    await call_repository.transition_call(call.id, CallState.RESERVED)
    call = await call_repository.transition_call(call.id, CallState.INITIATED)
    call = await call_repository.attach_provider_call_id(call.id, provider_call_id)

    agent = await agents.transition_agent(
        agent_id=agent.id,
        from_state=AgentState.RESERVED,
        to_state=AgentState.DIALING,
        actor=TransitionActor.ALLOCATOR,
        expected_version=agent.state_version,
    )

    return DialingCallContext(
        campaign=campaign,
        agent=agent,
        borrower=borrower,
        call=call,
        provider_call_id=provider_call_id,
        worker_id=worker_id,
    )


@pytest.fixture
def campaign_repository(test_database) -> CampaignRepository:
    return CampaignRepository(test_database)


@pytest.fixture
def decision_repository(test_database) -> DecisionRepository:
    return DecisionRepository(test_database)


@pytest.fixture
def health_manager(test_settings) -> ProviderHealthManager:
    return ProviderHealthManager(test_settings)


@pytest.fixture
def reservation_service(agent_repository, borrower_repository, test_settings):
    return ReservationService(agent_repository, borrower_repository, test_settings)


@pytest.fixture
def provider_registry(event_processor, health_manager, test_settings):
    async def on_event(event: ProviderEvent) -> None:
        health_manager.record_event_received(event.provider_name)
        await event_processor.process_event(event)

    return build_registry(on_event=on_event, seed=test_settings.PROVIDER_RANDOM_SEED)


HELD_CALL_SECONDS = 30.0


@pytest.fixture
async def fast_provider_registry(provider_registry):
    for name in provider_registry.names():
        provider = provider_registry.get(name)
        provider.behaviour.setup_latency_range = (0.0, 0.0)
        provider.behaviour.hang_rate = 0.0
        provider.behaviour.failure_rate = 0.0
        provider.behaviour.duplicate_rate = 0.0
        provider.behaviour.out_of_order_rate = 0.0
        provider.behaviour.ring_duration = HELD_CALL_SECONDS
        provider.behaviour.avg_talk_time = HELD_CALL_SECONDS
    yield provider_registry
    await provider_registry.shutdown()


@pytest.fixture
def call_allocator(
    reservation_service,
    call_repository,
    agent_repository,
    borrower_repository,
    fast_provider_registry,
    health_manager,
    retry_service,
    test_settings,
) -> CallAllocator:
    return CallAllocator(
        reservation_service=reservation_service,
        call_repository=call_repository,
        agent_repository=agent_repository,
        borrower_repository=borrower_repository,
        provider_registry=fast_provider_registry,
        health_manager=health_manager,
        retry_service=retry_service,
        settings=test_settings,
    )


@pytest.fixture
def safety_controller(
    agent_repository,
    call_repository,
    decision_repository,
    health_manager,
    availability_tracker,
    test_settings,
) -> SafetyController:
    return SafetyController(
        agent_repository=agent_repository,
        call_repository=call_repository,
        decision_repository=decision_repository,
        health_manager=health_manager,
        availability_tracker=availability_tracker,
        settings=test_settings,
    )


@pytest.fixture
def snapshot_builder(agent_repository, call_repository, health_manager, test_settings):
    return MetricsSnapshotBuilder(agent_repository, call_repository, health_manager, test_settings)


@pytest.fixture
def mode_router(snapshot_builder, safety_controller, call_allocator, decision_repository, test_settings):
    arguments = {
        "snapshot_builder": snapshot_builder,
        "safety_controller": safety_controller,
        "call_allocator": call_allocator,
        "decision_repository": decision_repository,
        "settings": test_settings,
    }
    return ModeRouter(
        progressive_dialer=ProgressiveDialer(**arguments),
        predictive_dialer=PredictiveDialer(**arguments),
    )


@pytest.fixture
def wrap_up_service(agent_repository, test_settings) -> WrapUpService:
    return WrapUpService(agent_repository, test_settings)


@pytest.fixture
def health_repository(test_database) -> HealthRepository:
    return HealthRepository(test_database)


@pytest.fixture
def retry_service(health_manager, test_settings) -> RetryService:
    return RetryService(health_manager, test_settings)


@pytest.fixture
def availability_tracker(test_settings) -> AgentAvailabilityTracker:
    return AgentAvailabilityTracker(test_settings)


@pytest.fixture
def recovery_worker(
    agent_repository,
    borrower_repository,
    call_repository,
    fast_provider_registry,
    retry_service,
    test_settings,
) -> RecoveryWorker:
    return RecoveryWorker(
        agent_repository=agent_repository,
        borrower_repository=borrower_repository,
        call_repository=call_repository,
        provider_registry=fast_provider_registry,
        retry_service=retry_service,
        settings=test_settings,
    )


@pytest.fixture
def metrics_registry() -> MetricsRegistry:
    return MetricsRegistry()


@pytest.fixture
def metrics_repository(test_database) -> MetricsRepository:
    return MetricsRepository(test_database)


@pytest.fixture
def metrics_collector(
    agent_repository, call_repository, decision_repository, metrics_registry, test_settings
) -> CampaignMetricsCollector:
    return CampaignMetricsCollector(
        agent_repository=agent_repository,
        call_repository=call_repository,
        decision_repository=decision_repository,
        registry=metrics_registry,
        settings=test_settings,
    )


@pytest.fixture
def metrics_sampler(
    campaign_repository, metrics_collector, metrics_repository, test_settings
) -> MetricsSampler:
    return MetricsSampler(
        campaign_repository=campaign_repository,
        metrics_collector=metrics_collector,
        metrics_repository=metrics_repository,
        settings=test_settings,
    )


API_TEST_KEY = "test-secret-key"


@pytest.fixture
def api_client(test_database):
    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


@pytest.fixture
def api_client_with_key(test_database, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app as fastapi_app

    monkeypatch.setenv("API_KEY", API_TEST_KEY)
    get_settings.cache_clear()
    try:
        with TestClient(fastapi_app) as client:
            yield client
    finally:
        get_settings.cache_clear()
