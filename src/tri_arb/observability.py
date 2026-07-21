"""Whitelisted JSON event logging without environment or request data."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TextIO

SAFE_FIELDS = {
    "active_count",
    "close_reason",
    "component",
    "deleted_count",
    "depth_book_count",
    "error",
    "error_type",
    "event_type",
    "lifecycle_id",
    "market_count",
    "metadata_rejection_count",
    "net_return_bps",
    "restart_closed_count",
    "route_count",
    "route_id",
    "shard_id",
    "state",
    "subscription_count",
    "ticker_count",
    "ticker_rejection_count",
}


class JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(
            timespec="milliseconds"
        )
        payload: dict[str, Any] = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": getattr(record, "event_name", record.getMessage()),
        }
        payload.update(getattr(record, "event_data", {}))
        if record.exc_info:
            error_type = record.exc_info[0]
            payload["exception_type"] = error_type.__name__ if error_type else "Exception"
            payload["exception_message"] = str(record.exc_info[1])
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def configure_logging(*, level: int = logging.INFO, stream: TextIO | None = None) -> None:
    logger = logging.getLogger("tri_arb")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonEventFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def get_logger(module_name: str) -> logging.Logger:
    name = module_name if module_name.startswith("tri_arb") else f"tri_arb.{module_name}"
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    event_name: str,
    *,
    level: int = logging.INFO,
    **fields: str | int | bool | Decimal | None,
) -> None:
    if not event_name or event_name != event_name.strip():
        raise ValueError("log event name must be non-empty and trimmed")
    unknown = set(fields) - SAFE_FIELDS
    if unknown:
        raise ValueError(f"unsafe structured log field(s): {', '.join(sorted(unknown))}")
    normalized: dict[str, str | int | bool | None] = {}
    for key, value in fields.items():
        if isinstance(value, Decimal):
            normalized[key] = str(value)
        elif isinstance(value, str):
            normalized[key] = value[:500]
        elif isinstance(value, (int, bool)) or value is None:
            normalized[key] = value
        else:
            raise TypeError(f"unsupported structured log value for {key}")
    logger.log(
        level,
        event_name,
        extra={"event_name": event_name, "event_data": normalized},
    )
