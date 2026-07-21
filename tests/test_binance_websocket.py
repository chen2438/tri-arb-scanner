import json

import pytest

from tri_arb.exchange.binance import BinanceDepthError
from tri_arb.exchange.binance.websocket import (
    control_payload,
    normalize_reference_event,
    stream_name,
    validate_control_message,
)


def test_builds_deterministic_public_depth_subscriptions() -> None:
    assert stream_name("BTCUSDT") == "btcusdt@depth@100ms"
    assert json.loads(control_payload("SUBSCRIBE", {"ETHUSDT", "BTCUSDT"}, 7)) == {
        "method": "SUBSCRIBE",
        "params": [
            "btcusdt@depth@100ms",
            "btcusdt@referencePrice",
            "ethusdt@depth@100ms",
            "ethusdt@referencePrice",
        ],
        "id": 7,
    }


def test_validates_control_acknowledgements_and_errors() -> None:
    assert validate_control_message({"result": None, "id": 1})
    assert not validate_control_message(
        {"e": "depthUpdate", "E": 1, "s": "BTCUSDT", "U": 1, "u": 1}
    )
    with pytest.raises(BinanceDepthError, match="rejected"):
        validate_control_message({"code": 2, "msg": "bad request", "id": 1})
    with pytest.raises(BinanceDepthError, match="invalid"):
        validate_control_message({"result": "ok", "id": 1})


def test_normalizes_timestamped_reference_price() -> None:
    reference = normalize_reference_event(
        {"e": "referencePrice", "s": "BTCUSDT", "r": "66451.5", "t": 1000},
        received_time_ms=1010,
    )
    assert str(reference.price) == "66451.5"
    assert reference.window_minutes == 0
    assert reference.source_time_ms == 1000
