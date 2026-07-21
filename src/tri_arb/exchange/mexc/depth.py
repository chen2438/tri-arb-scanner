"""Decode fail-closed MEXC partial-depth protobuf snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from google.protobuf.message import DecodeError

from tri_arb.domain.models import BookLevel, OrderBook
from tri_arb.exchange.mexc.proto.PushDataV3ApiWrapper_pb2 import PushDataV3ApiWrapper

DEPTH_LEVELS = 20
DEPTH_EVENT_TYPE = "spot@public.limit.depth.v3.api.pb"


class MexcDepthDecodeError(ValueError):
    """Raised when a public depth frame cannot be trusted."""


@dataclass(frozen=True, slots=True)
class DepthTiming:
    market_age_ms: int
    leg_skew_ms: int


class DepthTimingViolation(StrEnum):
    STALE = "stale_depth"
    LEG_SKEW = "leg_skew"


class DepthTimingError(ValueError):
    def __init__(self, violation: DepthTimingViolation, message: str) -> None:
        self.violation = violation
        super().__init__(message)


def depth_channel(symbol: str) -> str:
    if not symbol or symbol != symbol.strip() or symbol != symbol.upper():
        raise ValueError("invalid depth symbol")
    return f"{DEPTH_EVENT_TYPE}@{symbol}@{DEPTH_LEVELS}"


def _levels(items: object, *, side: str) -> tuple[BookLevel, ...]:
    raw_items = tuple(items)  # type: ignore[arg-type]
    if not 1 <= len(raw_items) <= DEPTH_LEVELS:
        raise MexcDepthDecodeError(f"{side} depth must contain 1 to {DEPTH_LEVELS} levels")
    levels: list[BookLevel] = []
    for item in raw_items:
        try:
            level = BookLevel(price=Decimal(item.price), quantity=Decimal(item.quantity))
        except (InvalidOperation, ValueError) as error:
            raise MexcDepthDecodeError(f"invalid {side} depth level") from error
        levels.append(level)
    return tuple(levels)


def decode_depth_frame(frame: bytes, *, received_time_ms: int) -> OrderBook:
    if not frame or len(frame) > 2 * 1024 * 1024:
        raise MexcDepthDecodeError("invalid depth frame size")
    if received_time_ms <= 0:
        raise ValueError("received_time_ms must be positive")
    wrapper = PushDataV3ApiWrapper()
    try:
        wrapper.ParseFromString(frame)
    except DecodeError as error:
        raise MexcDepthDecodeError("invalid protobuf frame") from error
    if wrapper.WhichOneof("body") != "publicLimitDepths":
        raise MexcDepthDecodeError("frame does not contain partial depth")
    if not wrapper.HasField("symbol") or not wrapper.HasField("sendTime"):
        raise MexcDepthDecodeError("depth frame is missing symbol or sendTime")
    symbol = wrapper.symbol
    if wrapper.channel != depth_channel(symbol):
        raise MexcDepthDecodeError("depth channel does not match symbol and level")
    if wrapper.sendTime <= 0:
        raise MexcDepthDecodeError("invalid depth sendTime")
    depth = wrapper.publicLimitDepths
    if (
        depth.eventType != DEPTH_EVENT_TYPE
        or not depth.version.isdecimal()
        or int(depth.version) <= 0
    ):
        raise MexcDepthDecodeError("invalid depth event type or version")
    try:
        return OrderBook(
            symbol=symbol,
            bids=_levels(depth.bids, side="bid"),
            asks=_levels(depth.asks, side="ask"),
            version=depth.version,
            source_time_ms=wrapper.sendTime,
            received_time_ms=received_time_ms,
        )
    except ValueError as error:
        raise MexcDepthDecodeError(str(error)) from error


def validate_depth_timing(
    books: Sequence[OrderBook],
    *,
    server_time_ms: int,
    max_age_ms: int = 2_000,
    max_leg_skew_ms: int = 1_000,
) -> DepthTiming:
    if len(books) != 3:
        raise ValueError("depth timing requires exactly three legs")
    if server_time_ms <= 0 or max_age_ms < 0 or max_leg_skew_ms < 0:
        raise ValueError("invalid depth timing boundary")
    source_times = [book.source_time_ms for book in books]
    ages = [abs(server_time_ms - source_time) for source_time in source_times]
    market_age = max(ages)
    leg_skew = max(source_times) - min(source_times)
    if market_age > max_age_ms:
        raise DepthTimingError(
            DepthTimingViolation.STALE,
            "depth snapshot is stale relative to MEXC server time",
        )
    if leg_skew > max_leg_skew_ms:
        raise DepthTimingError(
            DepthTimingViolation.LEG_SKEW,
            "depth legs exceed the maximum source-time skew",
        )
    return DepthTiming(market_age_ms=market_age, leg_skew_ms=leg_skew)
