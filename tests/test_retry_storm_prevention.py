import pytest

from app.models.base import utc_now
from app.models.borrower import Borrower
from app.models.enums import AgentState, BorrowerStatus, ProviderHealthStatus, SafetyVerdict
from app.safety.models import SafetyDecision
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

PROVIDER = "mock_b"
FAILING_CALLS = 50


def approve(campaign_id: str, approved: int) -> SafetyDecision:
    return SafetyDecision(
        campaign_id=campaign_id,
        requested=approved,
        approved=approved,
        verdict=SafetyVerdict.APPROVED,
        constraints=[],
        binding_constraint=None,
        snapshot_age_ms=0,
        created_at=utc_now(),
    )


def make_borrower(attempt_count: int) -> Borrower:
    return Borrower(
        campaign_id="campaign-1",
        name="Borrower",
        phone_number="+15550000001",
        attempt_count=attempt_count,
    )


def test_first_attempt_is_always_allowed(retry_service):
    assert retry_service.should_retry(make_borrower(0), PROVIDER) is True


def test_retry_is_blocked_once_attempts_are_exhausted(retry_service, test_settings):
    borrower = make_borrower(test_settings.MAX_CALL_ATTEMPTS)

    assert retry_service.should_retry(borrower, PROVIDER) is False


def test_retry_is_blocked_while_the_borrower_is_backing_off(retry_service):
    from datetime import timedelta

    borrower = make_borrower(1).model_copy(
        update={"next_eligible_at": utc_now() + timedelta(minutes=5)}
    )

    assert retry_service.should_retry(borrower, PROVIDER) is False


def test_retry_is_blocked_while_the_provider_is_unhealthy(retry_service, health_manager):
    for _ in range(10):
        health_manager.record_originate(PROVIDER, success=False, latency_ms=10)

    assert health_manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY
    assert retry_service.should_retry(make_borrower(1), PROVIDER) is False


def test_terminal_outcome_depends_on_whether_the_call_was_answered(retry_service):
    assert retry_service.outcome_for_terminal_call(answered=True).value == "CONTACTED"
    assert retry_service.outcome_for_terminal_call(answered=False).value == "RETRY"


async def test_no_retry_originates_are_attempted_while_unhealthy(
    test_database, call_allocator, fast_provider_registry, health_manager, test_settings
):
    campaign = await insert_campaign(test_database)
    campaign = campaign.model_copy(update={"provider_name": PROVIDER})
    await insert_agents(test_database, campaign.id, FAILING_CALLS, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, FAILING_CALLS)

    await test_database["borrowers"].update_many(
        {}, {"$set": {"attempt_count": 1, "next_eligible_at": utc_now()}}
    )
    for _ in range(10):
        health_manager.record_originate(PROVIDER, success=False, latency_ms=10)
    assert health_manager.get_health(PROVIDER).status is ProviderHealthStatus.UNHEALTHY

    originate_attempts_before = health_manager.get_health(PROVIDER).request_count
    result = await call_allocator.allocate(
        campaign, approve(campaign.id, FAILING_CALLS), "worker-1"
    )

    assert result.allocated == 0
    assert health_manager.get_health(PROVIDER).request_count == originate_attempts_before
    assert await test_database["calls"].count_documents({}) == 0

    borrowers = await test_database["borrowers"].find({}).to_list(None)
    assert all(borrower["status"] == BorrowerStatus.PENDING.value for borrower in borrowers)
    assert all(borrower["attempt_count"] == 1 for borrower in borrowers)


async def test_retries_resume_with_backoff_spread_after_recovery(
    test_database, call_allocator, health_manager, test_settings
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 5, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 5)
    await test_database["borrowers"].update_many(
        {}, {"$set": {"attempt_count": 1, "next_eligible_at": utc_now()}}
    )

    for _ in range(20):
        health_manager.record_originate(campaign.provider_name, success=True, latency_ms=10)
    assert health_manager.should_allow_retry(campaign.provider_name) is True

    result = await call_allocator.allocate(campaign, approve(campaign.id, 5), "worker-1")

    assert result.allocated == 5
    calls = await test_database["calls"].find({}).to_list(None)
    assert all(call["attempt"] == 2 for call in calls)


async def test_a_first_attempt_is_not_blocked_by_provider_health(
    test_database, call_allocator, health_manager
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 3, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 3)
    for _ in range(10):
        health_manager.record_originate(campaign.provider_name, success=False, latency_ms=10)

    result = await call_allocator.allocate(campaign, approve(campaign.id, 3), "worker-1")

    assert result.allocated == 3
