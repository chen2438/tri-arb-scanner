import json

import pytest

from tri_arb.exchange.bybit import BybitDepthError
from tri_arb.exchange.bybit.websocket import (
    BybitDepthWebSocketShard,
    control_payload,
    depth_message_symbol,
    topic,
    validate_control_message,
)


def test_builds_bounded_deterministic_public_subscriptions() -> None:
    assert topic("BTCUSDT") == "orderbook.200.BTCUSDT"
    assert json.loads(control_payload("subscribe", {"ETHUSDT", "BTCUSDT"}, "7")) == {
        "req_id": "7",
        "op": "subscribe",
        "args": ["orderbook.200.BTCUSDT", "orderbook.200.ETHUSDT"],
    }
    with pytest.raises(ValueError, match="invalid"):
        control_payload("subscribe", {f"ASSET{i}USDT" for i in range(11)}, "8")


def test_validates_control_acknowledgements_and_depth_topic() -> None:
    assert validate_control_message(
        {"success": True, "ret_msg": "", "op": "subscribe", "req_id": "7"}
    )
    assert depth_message_symbol({"topic": "orderbook.200.BTCUSDT"}) == "BTCUSDT"
    with pytest.raises(BybitDepthError, match="rejected"):
        validate_control_message(
            {"success": False, "ret_msg": "bad", "op": "subscribe"}
        )
    with pytest.raises(BybitDepthError, match="topic"):
        depth_message_symbol({"topic": "orderbook.50.BTCUSDT"})


def test_shard_accepts_at_most_thirty_unique_symbols() -> None:
    async def on_depth(_update):
        return None

    shard = BybitDepthWebSocketShard("wss://stream.bybit.test", 0, on_depth)
    shard.set_symbols(tuple(f"ASSET{i}USDT" for i in range(30)))
    with pytest.raises(ValueError, match="target symbols"):
        shard.set_symbols(tuple(f"ASSET{i}USDT" for i in range(31)))
