import pytest

from app.models.enums import AgentState, EventProcessingStatus
from app.providers.errors import ProviderTimeout
from app.simulation.agent_simulator import AgentSimulator
from app.simulation.config import SimulationConfig
from app.simulation.fault_injector import (
    FAULT_AGENT_AVAILABILITY_DROP,
    FAULT_DUPLICATE_EVENTS,
    FAULT_OUT_OF_ORDER_EVENTS,
    FAULT_PROVIDER_LATENCY_SPIKE,
    FAULT_PROVIDER_OUTAGE,
    FaultInjector,
)
from tests.conftest import insert_agents, insert_campaign, prepare_dialing_call

pytestmark = pytest.mark.usefixtures("clean_call_collections")


@pytest.fixture
def fault_injector(fast_provider_registry, event_repository, event_processor) -> FaultInjector:
    return FaultInjector(fast_provider_registry, event_repository, event_processor)


@pytest.fixture
def simulation_config() -> SimulationConfig:
    return SimulationConfig(name="test", agents=4, borrowers=10)


async def deliver_real_events(test_database, event_processor, call_repository):
    from datetime import datetime, timezone

    from app.providers.base import ProviderEvent

    context = await prepare_dialing_call(test_database, call_repository)
    for index, event_type in enumerate(["RINGING", "ANSWERED", "CONNECTED"]):
        await event_processor.process_event(
            ProviderEvent(
                provider_name="mock_a",
                provider_event_id=f"real-{index}",
                provider_call_id=context.provider_call_id,
                event_type=event_type,
                provider_timestamp=datetime.now(timezone.utc),
            )
        )
    return context


def test_latency_spike_multiplies_the_configured_latency(
    fault_injector, fast_provider_registry
):
    provider = fast_provider_registry.get("mock_a")
    provider.behaviour.setup_latency_range = (0.1, 0.2)

    result = fault_injector.provider_latency_spike("mock_a", multiplier=10.0)

    assert result.fault == FAULT_PROVIDER_LATENCY_SPIKE
    assert provider.behaviour.setup_latency_range == (1.0, 2.0)


async def test_forced_outage_makes_originates_time_out(
    fault_injector, fast_provider_registry
):
    from app.providers.base import OriginateRequest

    result = fault_injector.provider_outage("mock_b", seconds=30)
    provider = fast_provider_registry.get("mock_b")

    assert result.fault == FAULT_PROVIDER_OUTAGE
    assert result.affected == 1

    with pytest.raises(ProviderTimeout):
        await provider.originate_call(
            OriginateRequest(
                call_id="call-1",
                campaign_id="campaign-1",
                phone_number="+15550000001",
                timeout_seconds=0.01,
            )
        )


def test_outage_is_refused_for_a_provider_that_does_not_support_it(fault_injector):
    result = fault_injector.provider_outage("mock_a", seconds=30)

    assert result.affected == 0
    assert "does not support" in result.detail


async def test_duplicate_burst_replays_real_events_and_is_ignored(
    test_database, fault_injector, event_processor, call_repository
):
    context = await deliver_real_events(test_database, event_processor, call_repository)
    processed_before = await test_database["provider_events"].count_documents(
        {"processing_status": EventProcessingStatus.PROCESSED.value}
    )

    result = await fault_injector.duplicate_event_burst(context.provider_call_id)

    assert result.fault == FAULT_DUPLICATE_EVENTS
    assert result.affected == 3
    assert await test_database["provider_events"].count_documents({}) == 3
    assert (
        await test_database["provider_events"].count_documents(
            {"processing_status": EventProcessingStatus.PROCESSED.value}
        )
        == processed_before
    )


async def test_out_of_order_burst_leaves_the_call_consistent(
    test_database, fault_injector, event_processor, call_repository
):
    context = await deliver_real_events(test_database, event_processor, call_repository)
    call_before = await call_repository.find_by_id(context.call.id)

    result = await fault_injector.out_of_order_burst(context.provider_call_id)
    call_after = await call_repository.find_by_id(context.call.id)

    assert result.fault == FAULT_OUT_OF_ORDER_EVENTS
    assert call_after.state is call_before.state
    assert call_after.state_rank == call_before.state_rank


async def test_availability_drop_takes_real_agents_offline(
    test_database, fault_injector, agent_repository, simulation_config
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 10, state=AgentState.AVAILABLE)
    simulator = AgentSimulator(agent_repository, campaign.id, simulation_config)

    result = await fault_injector.agent_availability_drop(simulator, count=4)

    assert result.fault == FAULT_AGENT_AVAILABILITY_DROP
    assert result.affected == 4
    assert (
        await test_database["agents"].count_documents({"state": AgentState.OFFLINE.value}) == 4
    )
    assert (
        await test_database["agents"].count_documents({"state": AgentState.AVAILABLE.value}) == 6
    )


async def test_agent_simulator_logs_everyone_in_and_heartbeats(
    test_database, agent_repository, simulation_config
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.OFFLINE)
    simulator = AgentSimulator(agent_repository, campaign.id, simulation_config)

    logged_in = await simulator.log_everyone_in()

    assert logged_in == 5
    agents = await test_database["agents"].find({}).to_list(None)
    assert all(agent["state"] == AgentState.AVAILABLE.value for agent in agents)
    assert all(agent["last_heartbeat_at"] is not None for agent in agents)


def test_borrower_simulator_scales_provider_behaviour(fast_provider_registry):
    from app.simulation.borrower_simulator import configure_provider

    config = SimulationConfig(
        name="scaling",
        answer_rate=0.42,
        avg_talk_time_seconds=120.0,
        ring_duration_seconds=6.0,
        time_scale=60.0,
    )
    provider = fast_provider_registry.get("mock_a")

    configure_provider(provider, config)

    assert provider.behaviour.answer_rate == 0.42
    assert provider.behaviour.avg_talk_time == pytest.approx(2.0)
    assert provider.behaviour.ring_duration == pytest.approx(0.1)
