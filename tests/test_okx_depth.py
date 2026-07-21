from decimal import Decimal

import pytest

from tri_arb.exchange.okx import OkxDepthError, OkxOrderBookState
from tri_arb.exchange.okx.websocket import (
    control_payload,
    depth_message_symbol,
    validate_control_message,
)


def _message(
    *,
    action="snapshot",
    seq_id="10",
    prev_seq_id="-1",
    bids=None,
    asks=None,
):
    return {
        "arg": {"channel": "books", "instId": "BTC-USDT"},
        "action": action,
        "data": [
            {
                "bids": bids if bids is not None else [["100", "2", "0", "1"]],
                "asks": asks if asks is not None else [["101", "3", "0", "1"]],
                "ts": "1700000000000",
                "prevSeqId": prev_seq_id,
                "seqId": seq_id,
                "checksum": 0,
            }
        ],
    }


def test_builds_snapshot_and_merges_incremental_levels_with_deletions() -> None:
    state = OkxOrderBookState("BTC-USDT")
    snapshot = state.apply(
        _message(bids=[["100", "2"], ["99", "4"]], asks=[["101", "3"], ["102", "5"]]),
        received_time_ms=1700000000010,
    )
    updated = state.apply(
        _message(
            action="update",
            prev_seq_id="10",
            seq_id="11",
            bids=[["100", "0"], ["100.5", "7"]],
            asks=[["101", "6"]],
        ),
        received_time_ms=1700000000020,
    )

    assert snapshot.version == "10"
    assert [level.price for level in updated.bids] == [Decimal("100.5"), Decimal("99")]
    assert updated.asks[0].quantity == Decimal("6")
    assert updated.version == "11"


def test_rejects_update_before_snapshot_and_sequence_gap() -> None:
    state = OkxOrderBookState("BTC-USDT")
    with pytest.raises(OkxDepthError, match="before snapshot"):
        state.apply(
            _message(action="update", prev_seq_id="9", seq_id="10"),
            received_time_ms=1,
        )
    state.apply(_message(), received_time_ms=1)
    with pytest.raises(OkxDepthError, match="discontinuity"):
        state.apply(
            _message(action="update", prev_seq_id="8", seq_id="11"),
            received_time_ms=2,
        )


def test_rejects_crossed_book_wrong_channel_and_duplicate_prices() -> None:
    state = OkxOrderBookState("BTC-USDT")
    with pytest.raises(OkxDepthError, match="crossed"):
        state.apply(_message(bids=[["102", "1"]]), received_time_ms=1)
    wrong = _message()
    wrong["arg"]["channel"] = "books5"
    with pytest.raises(OkxDepthError, match="mismatch"):
        state.apply(wrong, received_time_ms=1)
    with pytest.raises(OkxDepthError, match="invalid bid level"):
        state.apply(_message(bids=[["100", "1"], ["100", "2"]]), received_time_ms=1)


def test_control_messages_are_deterministic_and_errors_fail_closed() -> None:
    assert control_payload("subscribe", {"ETH-USDT", "BTC-USDT"}) == (
        '{"op":"subscribe","args":[{"channel":"books","instId":"BTC-USDT"},'
        '{"channel":"books","instId":"ETH-USDT"}]}'
    )
    assert validate_control_message(
        {"event": "subscribe", "arg": {"channel": "books", "instId": "BTC-USDT"}}
    )
    with pytest.raises(ValueError, match="rejected"):
        validate_control_message({"event": "error", "code": "60012"})


def test_depth_message_symbol_validates_channel_and_identifier() -> None:
    assert depth_message_symbol(_message()) == "BTC-USDT"
    with pytest.raises(OkxDepthError, match="channel"):
        depth_message_symbol({"arg": {"channel": "books5", "instId": "BTC-USDT"}})
    with pytest.raises(OkxDepthError, match="symbol"):
        depth_message_symbol({"arg": {"channel": "books"}})
