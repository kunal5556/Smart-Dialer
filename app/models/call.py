from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from app.models.base import MongoModel, utc_now
from app.models.enums import CALL_STATE_RANK, TERMINAL_CALL_STATES, CallState


def build_idempotency_key(
    campaign_id: str,
    agent_id: str,
    borrower_id: str,
    attempt: int,
) -> str:
    return f"{campaign_id}:{agent_id}:{borrower_id}:{attempt}"


class Call(MongoModel):
    campaign_id: str
    agent_id: str
    borrower_id: str
    state: CallState = CallState.QUEUED
    state_rank: int = Field(default=CALL_STATE_RANK[CallState.QUEUED], ge=0)
    terminal: bool = False
    provider_name: str
    provider_call_id: str | None = None
    idempotency_key: str
    created_by_worker: str
    attempt: int = Field(default=1, ge=1)
    failure_reason: str | None = None
    retry_of_call_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    initiated_at: datetime | None = None
    ringing_at: datetime | None = None
    answered_at: datetime | None = None
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def derive_state_rank_and_terminal(self) -> Self:
        expected_rank = CALL_STATE_RANK[self.state]
        expected_terminal = self.state in TERMINAL_CALL_STATES
        if self.state_rank != expected_rank:
            object.__setattr__(self, "state_rank", expected_rank)
        if self.terminal != expected_terminal:
            object.__setattr__(self, "terminal", expected_terminal)
        return self
