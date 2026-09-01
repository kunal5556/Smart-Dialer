import pytest

from app.models.call import Call, build_idempotency_key
from app.models.enums import AgentState, CallState
from app.simulation.invariants import (
    INVARIANT_AGENT_DOUBLE_BOOKED,
    INVARIANT_BORROWER_DOUBLE_BOOKED,
    INVARIANT_CALLS_EXCEED_AGENTS,
    INVARIANT_STUCK_RESERVATION,
    InvariantChecker,
)
from tests.conftest import insert_agents, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")


@pytest.fixture
def invariant_checker(agent_repository, call_repository, test_settings) -> InvariantChecker:
    return InvariantChecker(agent_repository, call_repository, test_settings)


async def insert_call(
    test_database,
    campaign_id: str,
    agent_id: str,
    borrower_id: str,
    state: CallState = CallState.RINGING,
    attempt: int = 1,
) -> Call:
    call = Call(
        campaign_id=campaign_id,
        agent_id=agent_id,
        borrower_id=borrower_id,
        provider_name="mock_a",
        created_by_worker="worker-1",
        state=state,
        attempt=attempt,
        idempotency_key=build_idempotency_key(campaign_id, agent_id, borrower_id, attempt),
    )
    await test_database["calls"].insert_one(call.to_mongo())
    return call


async def test_a_healthy_campaign_reports_no_violations(
    test_database, invariant_checker
):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 3, state=AgentState.DIALING)
    for index, agent in enumerate(agents):
        await insert_call(test_database, campaign.id, agent.id, f"borrower-{index}")

    assert await invariant_checker.check(campaign.id) == []


async def test_an_agent_bound_to_two_active_calls_is_detected(
    test_database, invariant_checker
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.DIALING)
    await insert_call(test_database, campaign.id, agent.id, "borrower-1", attempt=1)
    await insert_call(test_database, campaign.id, agent.id, "borrower-2", attempt=2)

    violations = await invariant_checker.check(campaign.id)
    names = [violation.name for violation in violations]

    assert INVARIANT_AGENT_DOUBLE_BOOKED in names
    assert any(violation.offending_ids for violation in violations)


async def test_a_borrower_in_two_active_calls_is_detected(
    test_database, invariant_checker
):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(test_database, campaign.id, 2, state=AgentState.DIALING)
    await insert_call(test_database, campaign.id, agents[0].id, "borrower-1", attempt=1)
    await insert_call(test_database, campaign.id, agents[1].id, "borrower-1", attempt=2)

    names = [violation.name for violation in await invariant_checker.check(campaign.id)]

    assert INVARIANT_BORROWER_DOUBLE_BOOKED in names


async def test_more_calls_than_usable_agents_is_detected(
    test_database, invariant_checker
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.DIALING)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.OFFLINE)
    for index in range(3):
        await insert_call(
            test_database, campaign.id, f"agent-{index}", f"borrower-{index}"
        )

    names = [violation.name for violation in await invariant_checker.check(campaign.id)]

    assert INVARIANT_CALLS_EXCEED_AGENTS in names


async def test_offline_agents_do_not_count_as_usable_capacity(
    test_database, invariant_checker
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.OFFLINE)

    assert await invariant_checker.check(campaign.id) == []


async def test_terminal_calls_are_not_counted_as_active(
    test_database, invariant_checker
):
    campaign = await insert_campaign(test_database)
    [agent] = await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_call(
        test_database, campaign.id, agent.id, "borrower-1", state=CallState.COMPLETED
    )
    await insert_call(
        test_database, campaign.id, agent.id, "borrower-2", state=CallState.FAILED, attempt=2
    )

    assert await invariant_checker.check(campaign.id) == []


async def test_a_stuck_reservation_is_only_a_final_violation(
    test_database, invariant_checker
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.RESERVED)

    during = await invariant_checker.check(campaign.id)
    final = await invariant_checker.check(campaign.id, final=True)

    assert during == []
    assert INVARIANT_STUCK_RESERVATION in [violation.name for violation in final]
