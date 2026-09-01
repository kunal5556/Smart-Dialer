from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


class ProviderCallStatus(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OriginateRequest:
    call_id: str
    campaign_id: str
    phone_number: str
    timeout_seconds: float


@dataclass(frozen=True)
class OriginateResult:
    accepted: bool
    latency_ms: int
    provider_call_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ProviderEvent:
    provider_name: str
    provider_event_id: str
    provider_call_id: str
    event_type: str
    provider_timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider_name: str
    in_outage: bool
    originate_attempts: int
    originate_accepted: int
    originate_rejected: int
    originate_timed_out: int


EventCallback = Callable[[ProviderEvent], Awaitable[None]]


@runtime_checkable
class TelecomProvider(Protocol):
    name: str

    async def originate_call(self, request: OriginateRequest) -> OriginateResult: ...

    async def hangup_call(self, provider_call_id: str) -> None: ...

    async def get_call_status(self, provider_call_id: str) -> ProviderCallStatus: ...

    def health_snapshot(self) -> ProviderHealthSnapshot: ...

    async def shutdown(self) -> None: ...
