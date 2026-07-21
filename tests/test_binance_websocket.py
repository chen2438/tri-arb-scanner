import json

import pytest

from tri_arb.exchange.binance import BinanceDepthError
from tri_arb.exchange.binance.websocket import (
    control_payload,
    stream_name,
    validate_control_message,
)


def test_builds_deterministic_public_depth_subscriptions() -> None:
    assert stream_name("BTCUSDT") == "btcusdt@depth@100ms"
    assert json.loads(control_payload("SUBSCRIBE", {"ETHUSDT", "BTCUSDT"}, 7)) == {
        "method": "SUBSCRIBE",
        "params": ["btcusdt@depth@100ms", "ethusdt@depth@100ms"],
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
