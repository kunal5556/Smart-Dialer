import pytest

from app.metrics.utilization import (
    UTILIZATION_DENOMINATOR_STATES,
    agent_utilization,
    campaign_utilization,
)
from app.models.agent import Agent
from app.models.enums import AgentState

SECOND_MS = 1000


def make_agent(**overrides) -> Agent:
    fields = {"campaign_id": "campaign-1", "name": "Agent 001"}
    fields.update(overrides)
    return Agent(**fields)


def test_offline_and_paused_are_excluded_from_the_denominator():
    assert AgentState.OFFLINE not in UTILIZATION_DENOMINATOR_STATES
    assert AgentState.PAUSED not in UTILIZATION_DENOMINATOR_STATES
    assert UTILIZATION_DENOMINATOR_STATES == {
        AgentState.AVAILABLE,
        AgentState.RESERVED,
        AgentState.DIALING,
        AgentState.CONNECTED,
        AgentState.WRAP_UP,
    }


def test_sixty_seconds_connected_and_forty_available_is_sixty_percent():
    agent = make_agent(
        connected_time_ms=60 * SECOND_MS,
        busy_time_ms=60 * SECOND_MS,
        available_time_ms=40 * SECOND_MS,
    )

    utilization = agent_utilization(agent)

    assert utilization.counted_time_ms == 100 * SECOND_MS
    assert utilization.talk_utilization == pytest.approx(0.6)


def test_productive_utilization_includes_wrap_up():
    agent = make_agent(
        connected_time_ms=60 * SECOND_MS,
        wrap_up_time_ms=10 * SECOND_MS,
        busy_time_ms=70 * SECOND_MS,
        available_time_ms=30 * SECOND_MS,
    )

    utilization = agent_utilization(agent)

    assert utilization.talk_utilization == pytest.approx(0.6)
    assert utilization.productive_utilization == pytest.approx(0.7)


def test_offline_time_is_never_counted_because_it_is_never_accumulated():
    idle = make_agent(available_time_ms=0, busy_time_ms=0)

    utilization = agent_utilization(idle)

    assert utilization.counted_time_ms == 0
    assert utilization.talk_utilization is None


def test_missing_time_data_reports_null_rather_than_zero():
    utilization = agent_utilization(make_agent())

    assert utilization.talk_utilization is None
    assert utilization.productive_utilization is None


def test_campaign_utilization_sums_across_agents():
    agents = [
        make_agent(
            connected_time_ms=30 * SECOND_MS,
            busy_time_ms=30 * SECOND_MS,
            available_time_ms=70 * SECOND_MS,
        ),
        make_agent(
            connected_time_ms=90 * SECOND_MS,
            busy_time_ms=90 * SECOND_MS,
            available_time_ms=10 * SECOND_MS,
        ),
    ]

    utilization = campaign_utilization(agents)

    assert utilization.agents_counted == 2
    assert utilization.connected_time_ms == 120 * SECOND_MS
    assert utilization.counted_time_ms == 200 * SECOND_MS
    assert utilization.talk_utilization == pytest.approx(0.6)


def test_campaign_utilization_with_no_agents_is_null():
    utilization = campaign_utilization([])

    assert utilization.agents_counted == 0
    assert utilization.talk_utilization is None


async def test_time_accounting_accumulates_on_real_transitions(
    test_database, agent_repository, clean_call_collections
):
    import asyncio

    from app.state_machines.agent_sm import TransitionActor
    from tests.conftest import insert_agents, insert_campaign

    campaign = await insert_campaign(test_database)
    [seeded] = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)

    await asyncio.sleep(0.05)
    reserved = await agent_repository.try_reserve_agent(
        campaign_id=campaign.id,
        agent_id=seeded.id,
        worker_id="worker-1",
        ttl_seconds=30,
    )
    dialing = await agent_repository.transition_agent(
        agent_id=reserved.id,
        from_state=AgentState.RESERVED,
        to_state=AgentState.DIALING,
        actor=TransitionActor.ALLOCATOR,
        expected_version=reserved.state_version,
    )
    await asyncio.sleep(0.05)
    connected = await agent_repository.transition_agent(
        agent_id=dialing.id,
        from_state=AgentState.DIALING,
        to_state=AgentState.CONNECTED,
        actor=TransitionActor.EVENT_PROCESSOR,
        expected_version=dialing.state_version,
    )
    await asyncio.sleep(0.05)
    wrapped = await agent_repository.transition_agent(
        agent_id=connected.id,
        from_state=AgentState.CONNECTED,
        to_state=AgentState.WRAP_UP,
        actor=TransitionActor.EVENT_PROCESSOR,
        expected_version=connected.state_version,
    )

    assert wrapped.busy_time_ms > 0
    assert wrapped.connected_time_ms > 0
    assert wrapped.busy_time_ms >= wrapped.connected_time_ms
    assert agent_utilization(wrapped).talk_utilization is not None


async def test_offline_time_is_not_accumulated(
    test_database, agent_repository, clean_call_collections
):
    import asyncio

    from app.state_machines.agent_sm import TransitionActor
    from tests.conftest import insert_agents, insert_campaign

    campaign = await insert_campaign(test_database)
    [seeded] = await insert_agents(test_database, campaign.id, 1)

    await asyncio.sleep(0.05)
    online = await agent_repository.transition_agent(
        agent_id=seeded.id,
        from_state=AgentState.OFFLINE,
        to_state=AgentState.AVAILABLE,
        actor=TransitionActor.AGENT,
        expected_version=seeded.state_version,
    )

    assert online.busy_time_ms == 0
    assert online.available_time_ms == 0
