import asyncio

import pytest

from app.models.enums import AgentState, CallState
from app.providers.base import OriginateRequest
from app.providers.errors import ProviderTimeout
from app.providers.mock_b import MockProviderB
from app.repositories.agent_repo import AgentRepository
from app.repositories.borrower_repo import BorrowerRepository
from app.state_machines.agent_sm import TransitionActor
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

CALL_COUNT = 200
PROVIDER_NAME = "mock_b"


async def drain(provider, timeout_seconds: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while provider._pending_tasks and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)


@pytest.fixture
def soak_provider(event_processor):
    provider = MockProviderB(on_event=event_processor.process_event, seed=7)
    provider.behaviour.setup_latency_range = (0.0, 0.0)
    provider.behaviour.ring_duration = 0.0
    provider.behaviour.avg_talk_time = 0.0
    return provider


async def test_provider_b_soak_keeps_the_system_consistent(
    test_database, event_processor, call_repository, soak_provider, test_settings
):
    campaign = await insert_campaign(test_database)
    agents = await insert_agents(
        test_database, campaign.id, CALL_COUNT, state=AgentState.AVAILABLE
    )
    borrowers = await insert_borrowers(test_database, campaign.id, CALL_COUNT)

    agent_repository = AgentRepository(test_database)
    borrower_repository = BorrowerRepository(test_database)
    originated: list[str] = []

    for index in range(CALL_COUNT):
        worker_id = f"worker-{index}"
        agent = await agent_repository.try_reserve_agent(
            campaign_id=campaign.id,
            agent_id=agents[index].id,
            worker_id=worker_id,
            ttl_seconds=test_settings.RESERVATION_TTL_SECONDS,
        )
        borrower = await borrower_repository.try_reserve_borrower(
            campaign_id=campaign.id,
            borrower_id=borrowers[index].id,
            worker_id=worker_id,
            ttl_seconds=test_settings.RESERVATION_TTL_SECONDS,
        )
        call = await call_repository.create_call(
            campaign_id=campaign.id,
            agent_id=agent.id,
            borrower_id=borrower.id,
            provider_name=PROVIDER_NAME,
            worker_id=worker_id,
        )
        await call_repository.transition_call(call.id, CallState.RESERVED)
        await call_repository.transition_call(call.id, CallState.INITIATED)
        await agent_repository.transition_agent(
            agent_id=agent.id,
            from_state=AgentState.RESERVED,
            to_state=AgentState.DIALING,
            actor=TransitionActor.ALLOCATOR,
            expected_version=agent.state_version,
        )

        request = OriginateRequest(
            call_id=call.id,
            campaign_id=campaign.id,
            phone_number=borrower.phone_number,
            timeout_seconds=0.01,
        )
        try:
            result = await soak_provider.originate_call(request)
        except ProviderTimeout:
            await call_repository.transition_call(
                call.id, CallState.FAILED, failure_reason="provider_timeout"
            )
            continue

        if not result.accepted:
            await call_repository.transition_call(
                call.id, CallState.FAILED, failure_reason=result.error_code
            )
            continue

        await call_repository.attach_provider_call_id(call.id, result.provider_call_id)
        originated.append(call.id)

    await drain(soak_provider)
    await soak_provider.shutdown()

    assert len(originated) > CALL_COUNT // 2

    stored_calls = await test_database["calls"].find({}).to_list(None)
    assert len(stored_calls) == CALL_COUNT
    assert [call for call in stored_calls if not call["terminal"]] == []

    dialed_calls = [call for call in stored_calls if call["_id"] in set(originated)]
    assert len(dialed_calls) == len(originated)

    agent_ids = [call["agent_id"] for call in stored_calls]
    assert len(agent_ids) == len(set(agent_ids))

    dialed_agent_ids = {call["agent_id"] for call in dialed_calls}
    dialed_borrower_ids = {call["borrower_id"] for call in dialed_calls}

    stored_agents = await test_database["agents"].find(
        {"_id": {"$in": list(dialed_agent_ids)}}
    ).to_list(None)
    assert all(agent["reserved_by"] is None for agent in stored_agents)
    assert all(agent["current_call_id"] is None for agent in stored_agents)
    assert all(
        agent["state"] in {AgentState.AVAILABLE.value, AgentState.WRAP_UP.value}
        for agent in stored_agents
    )

    stored_borrowers = await test_database["borrowers"].find(
        {"_id": {"$in": list(dialed_borrower_ids)}}
    ).to_list(None)
    assert all(borrower["reserved_by"] is None for borrower in stored_borrowers)
    assert all(borrower["attempt_count"] == 1 for borrower in stored_borrowers)

    events = await test_database["provider_events"].find({}).to_list(None)
    assert events
    assert all(event["processing_status"] is not None for event in events)
    assert any(event["processing_status"] == "PROCESSED" for event in events)
    assert any(event["processing_status"] == "STALE_IGNORED" for event in events)

    emitted_event_ids = [event["provider_event_id"] for event in events]
    assert len(emitted_event_ids) == len(set(emitted_event_ids))
