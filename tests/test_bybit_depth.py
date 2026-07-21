from decimal import Decimal

import pytest

from tri_arb.exchange.bybit import (
    BybitDepthError,
    BybitOrderBookState,
    normalize_depth_snapshot,
)


def _payload():
    return {
        "s": "BTCUSDT",
        "b": [["100", "2"], ["99", "3"]],
        "a": [["101", "4"], ["102", "5"]],
        "ts": 1000,
        "u": 10,
        "seq": 20,
        "cts": 990,
    }


def test_normalizes_rest_depth_with_source_time_and_sequence() -> None:
    snapshot = normalize_depth_snapshot(_payload(), received_time_ms=1010)
    book = snapshot.to_order_book()

    assert book.bids[0].price == Decimal("100")
    assert book.asks[0].quantity == Decimal("4")
    assert book.version == "10:20"
    assert book.source_time_ms == 990


def test_rejects_crossed_or_unsorted_depth() -> None:
    crossed = _payload()
    crossed["a"] = [["100", "1"]]
    with pytest.raises(BybitDepthError, match="crossed"):
        normalize_depth_snapshot(crossed, received_time_ms=1010)

    unsorted = _payload()
    unsorted["b"] = [["99", "1"], ["100", "1"]]
    with pytest.raises(BybitDepthError, match="not sorted"):
        normalize_depth_snapshot(unsorted, received_time_ms=1010)


def _stream_message(*, action="snapshot", update_id=10, sequence=20, bids=None, asks=None):
    return {
        "topic": "orderbook.200.BTCUSDT",
        "type": action,
        "ts": 1000,
        "cts": 990,
        "data": {
            "s": "BTCUSDT",
            "b": bids if bids is not None else [["100", "2"]],
            "a": asks if asks is not None else [["101", "3"]],
            "u": update_id,
            "seq": sequence,
        },
    }


def test_reconstructs_snapshot_and_delta_with_deletions() -> None:
    state = BybitOrderBookState("BTCUSDT")
    state.apply(
        _stream_message(bids=[["100", "2"], ["99", "4"]]),
        received_time_ms=1000,
    )
    book = state.apply(
        _stream_message(
            action="delta",
            update_id=11,
            sequence=22,
            bids=[["100", "0"], ["100.5", "7"]],
            asks=[["101", "6"]],
        ),
        received_time_ms=1010,
    )

    assert [level.price for level in book.bids] == [Decimal("100.5"), Decimal("99")]
    assert book.asks[0].quantity == Decimal("6")
    assert book.version == "11:22"
    assert book.source_time_ms == 990


def test_rejects_delta_before_snapshot_and_out_of_order_update() -> None:
    state = BybitOrderBookState("BTCUSDT")
    with pytest.raises(BybitDepthError, match="before snapshot"):
        state.apply(_stream_message(action="delta"), received_time_ms=1000)
    state.apply(_stream_message(), received_time_ms=1000)
    with pytest.raises(BybitDepthError, match="out-of-order"):
        state.apply(
            _stream_message(action="delta", update_id=10, sequence=21),
            received_time_ms=1010,
        )
