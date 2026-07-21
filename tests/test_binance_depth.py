from decimal import Decimal

import pytest

from tri_arb.exchange.binance import (
    BinanceDepthError,
    BinanceOrderBookState,
    normalize_depth_event,
    normalize_depth_snapshot,
)


def _snapshot():
    return normalize_depth_snapshot(
        {
            "lastUpdateId": 100,
            "bids": [["100", "2"], ["99", "3"]],
            "asks": [["101", "4"], ["102", "5"]],
        },
        symbol="BTCUSDT",
        received_time_ms=1000,
    )


def _event(**overrides):
    payload = {
        "e": "depthUpdate",
        "E": 1010,
        "s": "BTCUSDT",
        "U": 101,
        "u": 102,
        "b": [["100", "0"], ["100.5", "7"]],
        "a": [["101", "6"]],
    }
    payload.update(overrides)
    return normalize_depth_event(payload, received_time_ms=1020)


def test_applies_bridging_diff_and_emits_timestamped_top_book() -> None:
    state = BinanceOrderBookState(_snapshot())
    book = state.apply(_event(U=99))

    assert book is not None
    assert [level.price for level in book.bids] == [Decimal("100.5"), Decimal("99")]
    assert book.asks[0].quantity == Decimal("6")
    assert book.version == "102"
    assert book.source_time_ms == 1010
    assert book.received_time_ms == 1020


def test_ignores_old_events_and_rejects_sequence_gap() -> None:
    state = BinanceOrderBookState(_snapshot())
    assert state.apply(_event(U=90, u=100)) is None
    with pytest.raises(BinanceDepthError, match="discontinuity"):
        state.apply(_event(U=102, u=103))


def test_rejects_unsorted_crossed_or_malformed_depth() -> None:
    with pytest.raises(BinanceDepthError, match="descending"):
        normalize_depth_snapshot(
            {"lastUpdateId": 1, "bids": [["99", "1"], ["100", "1"]], "asks": [["101", "1"]]},
            symbol="BTCUSDT",
            received_time_ms=1,
        )
    state = BinanceOrderBookState(_snapshot())
    with pytest.raises(BinanceDepthError, match="crossed"):
        state.apply(_event(b=[["101", "1"]]))
    with pytest.raises(BinanceDepthError, match="invalid depth event"):
        normalize_depth_event({"e": "trade"}, received_time_ms=1)
