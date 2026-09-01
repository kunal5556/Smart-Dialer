import logging

import pytest

from app.models.base import utc_now
from app.models.enums import AgentState, SafetyVerdict
from app.safety.models import SafetyDecision
from app.utils.redaction import REDACTED_PLACEHOLDER, redact_phone
from tests.conftest import insert_agents, insert_borrowers, insert_campaign

pytestmark = pytest.mark.usefixtures("clean_call_collections")

FULL_NUMBER = "+15551234567"


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


def test_only_the_last_four_digits_survive():
    assert redact_phone(FULL_NUMBER) == "****4567"


def test_short_and_missing_numbers_are_fully_redacted():
    assert redact_phone("123") == REDACTED_PLACEHOLDER
    assert redact_phone("") == REDACTED_PLACEHOLDER
    assert redact_phone(None) == REDACTED_PLACEHOLDER


def test_formatting_characters_do_not_leak_extra_digits():
    assert redact_phone("+1 (555) 123-4567") == "****4567"


async def test_no_full_phone_number_appears_in_logs_during_dialing(
    test_database, call_allocator, caplog
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 2, state=AgentState.AVAILABLE)
    borrowers = await insert_borrowers(test_database, campaign.id, 2)
    await test_database["borrowers"].update_many({}, {"$set": {"phone_number": FULL_NUMBER}})

    caplog.set_level(logging.DEBUG, logger="app")
    await call_allocator.allocate(campaign, approve(campaign.id, 2), "worker-1")

    captured = "\n".join(record.getMessage() for record in caplog.records)
    assert captured
    assert FULL_NUMBER not in captured
    assert "5551234567" not in captured
    assert "****4567" in captured

    _ = borrowers


async def test_no_full_phone_number_appears_in_logs_during_recovery(
    test_database, recovery_worker, call_repository, caplog
):
    from datetime import timedelta

    from tests.conftest import prepare_dialing_call

    context = await prepare_dialing_call(test_database, call_repository)
    await test_database["borrowers"].update_one(
        {"_id": context.borrower.id}, {"$set": {"phone_number": FULL_NUMBER}}
    )
    await test_database["agents"].update_one(
        {"_id": context.agent.id},
        {
            "$set": {
                "lease_expires_at": utc_now() - timedelta(seconds=60),
                "current_call_id": context.call.id,
            }
        },
    )

    caplog.set_level(logging.DEBUG, logger="app")
    await recovery_worker.run_sweeps()

    captured = "\n".join(record.getMessage() for record in caplog.records)
    assert FULL_NUMBER not in captured
    assert "5551234567" not in captured


async def test_log_records_carry_the_documented_context_fields(
    test_database, call_allocator, caplog
):
    campaign = await insert_campaign(test_database)
    await insert_agents(test_database, campaign.id, 1, state=AgentState.AVAILABLE)
    await insert_borrowers(test_database, campaign.id, 1)

    caplog.set_level(logging.INFO, logger="app")
    await call_allocator.allocate(campaign, approve(campaign.id, 1), "worker-1")

    initiated = [
        record for record in caplog.records if getattr(record, "event", "") == "call_initiated"
    ]
    assert initiated
    record = initiated[0]
    assert record.campaign_id == campaign.id
    assert record.agent_id is not None
    assert record.call_id is not None
    assert record.worker_id == "worker-1"
