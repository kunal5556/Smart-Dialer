AGENT_STATE_BADGES = {
    "OFFLINE": "⚪ OFFLINE",
    "AVAILABLE": "🟢 AVAILABLE",
    "RESERVED": "🟡 RESERVED",
    "DIALING": "🔵 DIALING",
    "CONNECTED": "🟣 CONNECTED",
    "WRAP_UP": "🟠 WRAP_UP",
    "PAUSED": "⚫ PAUSED",
}

CALL_STATE_BADGES = {
    "QUEUED": "⚪ QUEUED",
    "RESERVED": "🟡 RESERVED",
    "INITIATED": "🔵 INITIATED",
    "RINGING": "🔔 RINGING",
    "ANSWERED": "🟢 ANSWERED",
    "CONNECTED": "🟣 CONNECTED",
    "COMPLETED": "✅ COMPLETED",
    "FAILED": "❌ FAILED",
    "CANCELLED": "🚫 CANCELLED",
}

EVENT_STATUS_BADGES = {
    "PROCESSED": "✅ PROCESSED",
    "DUPLICATE_IGNORED": "🔁 DUPLICATE_IGNORED",
    "STALE_IGNORED": "⏮ STALE_IGNORED",
    "INVALID_IGNORED": "⚠ INVALID_IGNORED",
}

PROVIDER_STATUS_BADGES = {
    "HEALTHY": "🟢 HEALTHY",
    "DEGRADED": "🟠 DEGRADED",
    "UNHEALTHY": "🔴 UNHEALTHY",
}

VERDICT_BADGES = {
    "APPROVED": "✅ APPROVED",
    "REDUCED": "✂ REDUCED",
    "REJECTED": "⛔ REJECTED",
    "FALLBACK_PROGRESSIVE": "🛟 FALLBACK_PROGRESSIVE",
}

EMPTY = "—"


def badge(value: str | None, table: dict[str, str]) -> str:
    if not value:
        return EMPTY
    return table.get(value, value)


def agent_state(value: str | None) -> str:
    return badge(value, AGENT_STATE_BADGES)


def call_state(value: str | None) -> str:
    return badge(value, CALL_STATE_BADGES)


def event_status(value: str | None) -> str:
    return badge(value, EVENT_STATUS_BADGES)


def provider_status(value: str | None) -> str:
    return badge(value, PROVIDER_STATUS_BADGES)


def verdict(value: str | None) -> str:
    return badge(value, VERDICT_BADGES)


def percentage(value: float | None, digits: int = 1) -> str:
    if value is None:
        return EMPTY
    return f"{value * 100:.{digits}f}%"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return EMPTY
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}m {remainder}s"


def milliseconds(value: float | None) -> str:
    if value is None:
        return EMPTY
    return f"{value:.0f} ms"


def number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return EMPTY
    if isinstance(value, bool):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.{digits}f}"


def timestamp(value: str | None) -> str:
    if not value:
        return EMPTY
    return value.replace("T", " ")[:19]
