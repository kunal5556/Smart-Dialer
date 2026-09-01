import ast
import asyncio
import inspect
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.models.base import utc_now
from app.models.enums import (
    AgentState,
    BorrowerStatus,
    CallState,
    DialingMode,
    EventProcessingStatus,
    SafetyVerdict,
)
from app.providers.base import ProviderEvent
from app.safety.models import PacingRequest
from app.services.call_allocator import CallAllocator
from tests.conftest import (
    insert_agents,
    insert_borrowers,
    insert_campaign,
    prepare_dialing_call,
)

pytestmark = pytest.mark.usefixtures("clean_call_collections")

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]

NON_TERMINAL_CALL_STATES = [
    CallState.QUEUED.value,
    CallState.RESERVED.value,
    CallState.INITIATED.value,
    CallState.RINGING.value,
    CallState.ANSWERED.value,
    CallState.CONNECTED.value,
]


def make_event(provider_call_id: str, event_type: str, event_id: str) -> ProviderEvent:
    return ProviderEvent(
        provider_name="mock_a",
        provider_event_id=event_id,
        provider_call_id=provider_call_id,
        event_type=event_type,
        provider_timestamp=datetime.now(timezone.utc),
    )


def make_request(requested: int, mode: DialingMode = DialingMode.PREDICTIVE) -> PacingRequest:
    return PacingRequest(
        requested=requested,
        mode=mode,
        snapshot_captured_at=utc_now(),
        inputs={},
        explanation="acceptance check",
    )


async def test_safety_the_predictive_engine_cannot_bypass_the_safety_controller(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)

    inflated = PacingRequest(
        requested=500,
        mode=DialingMode.PREDICTIVE,
        snapshot_captured_at=utc_now(),
        inputs={"available_agents": 500, "free_capacity": 500},
        explanation="a deliberately dishonest request",
    )
    decision = await safety_controller.evaluate(campaign, inflated)

    assert decision.approved == 3

    signature = inspect.signature(CallAllocator.allocate)
    assert "SafetyDecision" in str(signature.parameters["decision"].annotation)

    for package in ("pacing", "safety"):
        for path in (REPOSITORY_ROOT / "app" / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.Import):
                    module = node.names[0].name
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                if module:
                    assert not module.startswith("app.providers")
                    assert module != "app.services.call_allocator"


@pytest.mark.parametrize("agent_count", [1, 5, 10])
async def test_progressive_agent_bound_calls_never_exceed_available_agents(
    test_database, mode_router, agent_count
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, agent_count, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 500)
    dialer = mode_router.select(campaign)

    for _ in range(3):
        await dialer.tick(campaign, "worker-1")
        bound = await test_database["calls"].count_documents(
            {"state": {"$in": NON_TERMINAL_CALL_STATES}}
        )
        assert bound <= agent_count


async def test_concurrency_two_workers_cannot_reserve_the_same_agent(
    test_database, agent_repository
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)

    results = await asyncio.gather(
        *(
            agent_repository.try_reserve_agent(
                campaign_id=campaign.id,
                agent_id=agent.id,
                worker_id=f"worker-{index}",
                ttl_seconds=30,
            )
            for index in range(20)
        )
    )

    assert len([item for item in results if item is not None]) == 1


async def test_concurrency_a_borrower_cannot_be_double_allocated(
    test_database, borrower_repository
):
    campaign = await insert_campaign(test_database)
    [borrower] = await insert_borrowers(test_database, campaign.id, 1)

    results = await asyncio.gather(
        *(
            borrower_repository.try_reserve_borrower(
                campaign_id=campaign.id,
                borrower_id=borrower.id,
                worker_id=f"worker-{index}",
                ttl_seconds=30,
            )
            for index in range(20)
        )
    )

    assert len([item for item in results if item is not None]) == 1


async def test_idempotency_duplicate_events_cause_no_duplicate_effects(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    event = make_event(context.provider_call_id, "ANSWERED", "acceptance-duplicate")

    statuses = [(await event_processor.process_event(event)).status for _ in range(5)]

    assert statuses[0] is EventProcessingStatus.PROCESSED
    assert statuses[1:] == [EventProcessingStatus.DUPLICATE_IGNORED] * 4

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    assert agent["state_version"] == context.agent.state_version + 1
    assert await test_database["provider_events"].count_documents({}) == 1


async def test_ordering_out_of_order_events_do_not_corrupt_state(
    test_database, event_processor, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)

    for index, event_type in enumerate(["COMPLETED", "ANSWERED", "RINGING"]):
        await event_processor.process_event(
            make_event(context.provider_call_id, event_type, f"acceptance-order-{index}")
        )

    call = await call_repository.find_by_id(context.call.id)
    assert call.state is CallState.COMPLETED
    assert call.terminal is True

    ignored = await test_database["provider_events"].count_documents(
        {"processing_status": EventProcessingStatus.STALE_IGNORED.value}
    )
    assert ignored == 2


async def test_recovery_worker_crashes_leak_no_reservations(
    test_database, recovery_worker, call_repository
):
    context = await prepare_dialing_call(test_database, call_repository)
    expired = utc_now() - timedelta(seconds=120)
    await test_database["agents"].update_one(
        {"_id": context.agent.id},
        {"$set": {"lease_expires_at": expired, "current_call_id": context.call.id}},
    )
    await test_database["borrowers"].update_one(
        {"_id": context.borrower.id}, {"$set": {"lease_expires_at": expired}}
    )

    await recovery_worker.run_sweeps()

    agent = await test_database["agents"].find_one({"_id": context.agent.id})
    borrower = await test_database["borrowers"].find_one({"_id": context.borrower.id})
    call = await call_repository.find_by_id(context.call.id)

    assert agent["state"] == AgentState.AVAILABLE.value
    assert agent["reserved_by"] is None
    assert borrower["status"] == BorrowerStatus.PENDING.value
    assert borrower["reserved_by"] is None
    assert call.terminal is True


async def test_provider_outage_causes_conservative_behaviour(
    test_database, safety_controller, health_manager
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 20, state=AgentState.AVAILABLE)
    for _ in range(10):
        health_manager.record_originate(campaign.provider_name, success=False, latency_ms=10)

    decision = await safety_controller.evaluate(campaign, make_request(50))

    assert decision.approved == 0
    assert decision.verdict is SafetyVerdict.REJECTED
    assert health_manager.should_allow_retry(campaign.provider_name) is False


async def test_agent_availability_drops_are_reflected_quickly(
    test_database, safety_controller
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"max_concurrent_calls": 500})
    agents = await insert_agents(test_database, campaign.id, 100, state=AgentState.AVAILABLE)

    await safety_controller.evaluate(campaign, make_request(1))
    await test_database["agents"].update_many(
        {"_id": {"$in": [agent.id for agent in agents[:40]]}},
        {"$set": {"state": AgentState.OFFLINE.value}},
    )
    decision = await safety_controller.evaluate(campaign, make_request(200))

    assert decision.approved == 60
    assert decision.verdict is SafetyVerdict.FALLBACK_PROGRESSIVE
    assert decision.fallback_reason == "availability_drop"


async def test_predictive_pacing_is_explainable(
    test_database, mode_router, decision_repository
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 50)

    await mode_router.select(campaign).tick(campaign, "worker-1")

    [decision] = await decision_repository.find_recent_pacing_decisions(campaign.id, limit=1)

    assert decision.explanation
    assert str(decision.requested) in decision.explanation
    for field in ("available_agents", "effective_answer_rate", "safety_margin", "requested"):
        assert decision.inputs[field] is not None


async def test_utilization_and_metrics_are_measurable(test_database, metrics_collector):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await test_database["agents"].update_many(
        {},
        {
            "$set": {
                "connected_time_ms": 60000,
                "busy_time_ms": 60000,
                "available_time_ms": 40000,
            }
        },
    )

    metrics = await metrics_collector.collect(campaign)

    assert metrics.talk_utilization == pytest.approx(0.6, abs=0.01)
    assert set(metrics.agent_states) == {state.value for state in AgentState}
    assert metrics.peak_concurrent_calls >= 0


def test_scaling_bottlenecks_are_documented_with_measured_numbers():
    document = (REPOSITORY_ROOT / "docs" / "scalability.md").read_text(encoding="utf-8")

    assert "dialer_tick" in document
    assert "10 000" in document
    assert "add more servers" in document.lower()
    for scale in ("100", "1 000", "10 000"):
        assert scale in document


def test_the_dashboard_cannot_bypass_the_safety_controller():
    dashboard_root = REPOSITORY_ROOT / "dashboard"
    forbidden = ("app", "motor", "pymongo", "fastapi")

    for path in dashboard_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                module = node.names[0].name
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            if module:
                assert module.split(".")[0] not in forbidden, f"{path.name} imports {module}"


def test_another_engineer_can_run_it():
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "uvicorn app.main:app" in readme
    assert "streamlit run dashboard/app.py" in readme
    assert "pip install -r requirements" in readme
    assert "MONGODB_URI" in readme
