import pytest

from app.models.enums import AgentState, DialingMode
from tests.conftest import insert_agents, insert_borrowers, insert_campaign
from tests.test_pacing_engine import BASE_CONFIG, make_snapshot

from app.pacing.pacing_engine import compute_request

pytestmark = pytest.mark.usefixtures("clean_call_collections")

REQUIRED_INPUT_FIELDS = [
    "available_agents",
    "active_calls",
    "ringing_calls",
    "historical_answer_rate",
    "effective_answer_rate",
    "avg_talk_time_seconds",
    "avg_setup_time_ms",
    "provider_status",
    "safety_margin",
    "calls_needed",
    "requested",
]


def test_every_documented_input_is_captured():
    request = compute_request(make_snapshot(), BASE_CONFIG)

    for field in REQUIRED_INPUT_FIELDS:
        assert field in request.inputs, f"missing explainability field: {field}"
        assert request.inputs[field] is not None


def test_explanation_answers_why_that_number():
    request = compute_request(make_snapshot(), BASE_CONFIG)

    assert request.explanation
    assert str(request.requested) in request.explanation
    assert "safety margin" in request.explanation
    assert "answer rate" in request.explanation


def test_explanation_matches_the_roadmap_worked_example():
    request = compute_request(make_snapshot(), BASE_CONFIG)

    assert request.explanation == (
        "12 agents free + 3 soon-free (weighted 1.5) = 13.5 capacity; "
        "at 32% estimated answer rate that needs 42 calls; "
        "21 already in flight leaves 21; "
        "x0.85 safety margin x1 health x1 volatility = 17 requested."
    )


async def test_pacing_decision_is_persisted_with_all_inputs(
    test_database, mode_router, decision_repository
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 6, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 20)

    await mode_router.select(campaign).tick(campaign, "worker-1")

    stored = await decision_repository.find_recent_pacing_decisions(campaign.id, limit=5)
    assert len(stored) == 1
    decision = stored[0]
    assert decision.dialing_mode is DialingMode.PROGRESSIVE
    assert decision.explanation
    for field in REQUIRED_INPUT_FIELDS:
        assert field in decision.inputs


async def test_pacing_and_safety_decisions_are_linked(
    test_database, mode_router, decision_repository
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 10)

    result = await mode_router.select(campaign).tick(campaign, "worker-1")

    pacing = await decision_repository.find_recent_pacing_decisions(campaign.id, limit=1)
    safety = await decision_repository.find_recent_safety_decisions(campaign.id, limit=1)

    assert safety[0].pacing_decision_id == pacing[0].id
    assert safety[0].requested == pacing[0].requested
    assert safety[0].approved == result.decision.approved


async def test_the_stored_decision_pair_explains_a_reduction(
    test_database, mode_router, decision_repository
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"dialing_mode": DialingMode.PREDICTIVE})
    await test_database["campaigns"].update_one(
        {"_id": campaign.id}, {"$set": {"dialing_mode": DialingMode.PREDICTIVE.value}}
    )
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 50)

    await mode_router.select(campaign).tick(campaign, "worker-1")

    pacing = (await decision_repository.find_recent_pacing_decisions(campaign.id, limit=1))[0]
    safety = (await decision_repository.find_recent_safety_decisions(campaign.id, limit=1))[0]

    assert pacing.requested > safety.approved
    assert safety.binding_constraint is not None
    assert any(constraint.binding for constraint in safety.constraints)
