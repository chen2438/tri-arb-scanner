import io
import json
import logging
from decimal import Decimal

import pytest

from tri_arb.observability import configure_logging, get_logger, log_event


def test_emits_whitelisted_decimal_safe_json_event() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    log_event(
        get_logger("test"),
        "scanner.lifecycle_event",
        lifecycle_id="id-1",
        route_id="route-1",
        event_type="opened",
        net_return_bps=Decimal("20.50"),
    )

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "info"
    assert payload["event"] == "scanner.lifecycle_event"
    assert payload["net_return_bps"] == "20.50"
    assert "environment" not in payload


@pytest.mark.parametrize("field", ["authorization", "headers", "api_key", "database"])
def test_rejects_arbitrary_or_sensitive_structured_fields(field: str) -> None:
    with pytest.raises(ValueError, match="unsafe structured log"):
        log_event(get_logger("test"), "unsafe", **{field: "secret"})


def test_warning_level_is_encoded_without_record_internals() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.WARNING, stream=stream)

    log_event(
        get_logger("test"),
        "market_data.error",
        level=logging.WARNING,
        component="metadata",
        error_type="TimeoutError",
        error="timed out",
    )

    payload = json.loads(stream.getvalue())
    assert payload == {
        "component": "metadata",
        "error": "timed out",
        "error_type": "TimeoutError",
        "event": "market_data.error",
        "level": "warning",
        "logger": "tri_arb.test",
        "timestamp": payload["timestamp"],
    }


def test_bounds_external_error_text() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    log_event(get_logger("test"), "market_data.error", error="x" * 1_000)

    assert len(json.loads(stream.getvalue())["error"]) == 500
