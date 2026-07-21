import base64
from decimal import Decimal
from pathlib import Path

import pytest

from tri_arb.domain.models import BookLevel, OrderBook
from tri_arb.exchange.mexc import (
    MexcDepthDecodeError,
    decode_depth_frame,
    validate_depth_timing,
)
from tri_arb.exchange.mexc.proto.PushDataV3ApiWrapper_pb2 import PushDataV3ApiWrapper

FIXTURE = Path(__file__).parent / "fixtures" / "mexc_btcusdt_depth_20.b64"


def _frame() -> bytes:
    return base64.b64decode(FIXTURE.read_text(encoding="ascii"))


def test_decodes_recorded_public_mexc_twenty_level_snapshot() -> None:
    book = decode_depth_frame(_frame(), received_time_ms=1_784_637_652_400)

    assert book.symbol == "BTCUSDT"
    assert book.version == "77345964963"
    assert book.source_time_ms == 1_784_637_652_372
    assert len(book.bids) == 20
    assert len(book.asks) == 20
    assert book.bids[0] == BookLevel(Decimal("66626.76"), Decimal("7.94505061"))
    assert book.asks[0] == BookLevel(Decimal("66626.77"), Decimal("0.00033100"))


@pytest.mark.parametrize("mutation", ["channel", "body", "version", "send_time"])
def test_rejects_incomplete_or_mismatched_depth_frames(mutation: str) -> None:
    wrapper = PushDataV3ApiWrapper.FromString(_frame())
    if mutation == "channel":
        wrapper.channel = "spot@public.limit.depth.v3.api.pb@ETHUSDT@20"
    elif mutation == "body":
        wrapper.ClearField("publicLimitDepths")
    elif mutation == "version":
        wrapper.publicLimitDepths.version = ""
    else:
        wrapper.sendTime = 0

    with pytest.raises(MexcDepthDecodeError):
        decode_depth_frame(wrapper.SerializeToString(), received_time_ms=1)


@pytest.mark.parametrize("field", ["symbol", "price", "quantity", "version", "magnitude"])
def test_rejects_oversized_depth_text_fields(field: str) -> None:
    wrapper = PushDataV3ApiWrapper.FromString(_frame())
    if field == "symbol":
        wrapper.symbol = "S" * 65
    elif field == "version":
        wrapper.publicLimitDepths.version = "1" * 65
    elif field in {"price", "quantity"}:
        setattr(wrapper.publicLimitDepths.bids[0], field, "1" * 129)
    else:
        wrapper.publicLimitDepths.bids[0].price = "1e999"

    with pytest.raises(MexcDepthDecodeError):
        decode_depth_frame(wrapper.SerializeToString(), received_time_ms=1)


def _book(symbol: str, source_time_ms: int) -> OrderBook:
    return OrderBook(
        symbol=symbol,
        bids=(BookLevel(Decimal("1"), Decimal("1")),),
        asks=(BookLevel(Decimal("2"), Decimal("1")),),
        version="1",
        source_time_ms=source_time_ms,
        received_time_ms=source_time_ms,
    )


def test_validates_three_leg_server_age_and_source_skew() -> None:
    books = (_book("AUSDT", 10_000), _book("BA", 9_800), _book("BUSDT", 9_500))

    timing = validate_depth_timing(books, server_time_ms=10_100)

    assert timing.market_age_ms == 600
    assert timing.leg_skew_ms == 500
    with pytest.raises(ValueError, match="stale"):
        validate_depth_timing(books, server_time_ms=12_001)
    with pytest.raises(ValueError, match="skew"):
        validate_depth_timing(books, server_time_ms=10_100, max_leg_skew_ms=499)
