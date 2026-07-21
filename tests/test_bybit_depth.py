from decimal import Decimal

import pytest

from tri_arb.exchange.bybit import BybitDepthError, normalize_depth_snapshot


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
