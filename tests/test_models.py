import pytest

from app.models import (
    CALL_STATE_RANK,
    TERMINAL_CALL_STATES,
    Agent,
    AgentState,
    Borrower,
    BorrowerStatus,
    Call,
    CallState,
    Campaign,
    CampaignStatus,
    DialingMode,
    PacingDecision,
    ProviderEvent,
    SafetyConstraintRecord,
    SafetyDecision,
    SafetyVerdict,
    build_idempotency_key,
)


def build_call(**overrides) -> Call:
    fields = {
        "campaign_id": "campaign-1",
        "agent_id": "agent-1",
        "borrower_id": "borrower-1",
        "provider_name": "mock_a",
        "idempotency_key": build_idempotency_key("campaign-1", "agent-1", "borrower-1", 1),
        "created_by_worker": "worker-1",
    }
    fields.update(overrides)
    return Call(**fields)


def sample_models() -> list:
    return [
        Campaign(name="Demo"),
        Agent(campaign_id="campaign-1", name="Agent 001"),
        Borrower(campaign_id="campaign-1", name="Borrower 0001", phone_number="+15550000001"),
        build_call(),
        ProviderEvent(
            provider_name="mock_a",
            provider_event_id="event-1",
            provider_call_id="provider-call-1",
            event_type="ANSWERED",
        ),
        PacingDecision(
            campaign_id="campaign-1",
            dialing_mode=DialingMode.PREDICTIVE,
            requested=17,
            inputs={"available_agents": 12, "effective_answer_rate": 0.32},
            explanation="12 free agents at 32 percent answer rate",
        ),
        SafetyDecision(
            campaign_id="campaign-1",
            requested=17,
            approved=8,
            verdict=SafetyVerdict.REDUCED,
            constraints=[SafetyConstraintRecord(name="agent_capacity", limit=8, binding=True)],
            binding_constraint="agent_capacity",
        ),
    ]


@pytest.mark.parametrize("model", sample_models())
def test_models_round_trip_through_mongo_documents(model):
    document = model.to_mongo()

    assert "_id" in document
    assert document["_id"] == model.id
    assert type(model).from_mongo(document) == model


def test_default_states_match_the_domain_model():
    assert Campaign(name="Demo").status is CampaignStatus.DRAFT
    assert Campaign(name="Demo").dialing_mode is DialingMode.PROGRESSIVE
    assert Agent(campaign_id="c", name="a").state is AgentState.OFFLINE
    assert Borrower(campaign_id="c", name="b", phone_number="+1").status is BorrowerStatus.PENDING
    assert build_call().state is CallState.QUEUED


def test_every_call_state_has_a_rank():
    assert set(CALL_STATE_RANK) == set(CallState)


def test_terminal_call_states_are_ranked_six():
    assert TERMINAL_CALL_STATES == {CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED}
    for state in TERMINAL_CALL_STATES:
        assert CALL_STATE_RANK[state] == 6
    for state in set(CallState) - TERMINAL_CALL_STATES:
        assert CALL_STATE_RANK[state] < 6


@pytest.mark.parametrize("state", list(CallState))
def test_call_rank_and_terminal_flag_follow_the_state(state):
    call = build_call(state=state)

    assert call.state_rank == CALL_STATE_RANK[state]
    assert call.terminal is (state in TERMINAL_CALL_STATES)


def test_call_rejects_a_rank_that_disagrees_with_its_state():
    call = build_call(state=CallState.RINGING, state_rank=0, terminal=True)

    assert call.state_rank == CALL_STATE_RANK[CallState.RINGING]
    assert call.terminal is False


def test_idempotency_key_uses_the_documented_format():
    assert build_idempotency_key("c1", "a1", "b1", 2) == "c1:a1:b1:2"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValueError):
        Agent(campaign_id="c", name="a", unexpected_field=True)
