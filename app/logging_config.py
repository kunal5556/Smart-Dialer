import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

CONTEXT_FIELDS = ("campaign_id", "agent_id", "borrower_id", "call_id", "worker_id")

NOISY_LOGGERS = ("pymongo", "motor", "asyncio")


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            payload[field] = getattr(record, field, None)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    from app.config import get_settings

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(get_settings().LOG_LEVEL.upper())

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **context: Any,
) -> None:
    unknown = set(context) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported log context fields: {sorted(unknown)}")
    logger.log(level, message, extra={"event": event, **context})
