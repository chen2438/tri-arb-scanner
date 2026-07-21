"""Bybit V5 public REST depth snapshot normalization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import BookLevel, OrderBook

MAX_DEPTH_LEVELS = 1_000
MAX_DECIMAL_LENGTH = 128


class BybitDepthError(ValueError):
    pass


def _positive_int(raw: Mapping[str, Any], field: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BybitDepthError(f"invalid {field}")
    return value


def _levels(raw: Any, *, side: str) -> tuple[BookLevel, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_DEPTH_LEVELS:
        raise BybitDepthError(f"invalid {side} levels")
    levels: list[BookLevel] = []
    for index, item in enumerate(raw):
        if not isinstance(item, list) or len(item) != 2:
            raise BybitDepthError(f"invalid {side} level at index {index}")
        price_raw, quantity_raw = item
        if not all(
            isinstance(value, str) and 0 < len(value) <= MAX_DECIMAL_LENGTH
            for value in item
        ):
            raise BybitDepthError(f"invalid {side} level at index {index}")
        try:
            level = BookLevel(Decimal(price_raw), Decimal(quantity_raw))
        except (InvalidOperation, ValueError) as error:
            raise BybitDepthError(f"invalid {side} level at index {index}") from error
        levels.append(level)
    reverse = side == "bids"
    if levels != sorted(levels, key=lambda level: level.price, reverse=reverse):
        raise BybitDepthError(f"{side} levels are not sorted")
    if len({level.price for level in levels}) != len(levels):
        raise BybitDepthError(f"duplicate {side} price")
    return tuple(levels)


@dataclass(frozen=True, slots=True)
class BybitDepthSnapshot:
    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    update_id: int
    sequence: int
    source_time_ms: int
    received_time_ms: int

    def to_order_book(self, *, levels: int = 20) -> OrderBook:
        if not 1 <= levels <= MAX_DEPTH_LEVELS:
            raise ValueError("levels must be between 1 and 1000")
        return OrderBook(
            self.symbol,
            self.bids[:levels],
            self.asks[:levels],
            f"{self.update_id}:{self.sequence}",
            self.source_time_ms,
            self.received_time_ms,
        )


def normalize_depth_snapshot(
    payload: Any,
    *,
    received_time_ms: int,
) -> BybitDepthSnapshot:
    if not isinstance(payload, Mapping):
        raise BybitDepthError("depth result must be an object")
    symbol = payload.get("s")
    if (
        not isinstance(symbol, str)
        or not symbol
        or symbol != symbol.strip().upper()
        or len(symbol) > 64
    ):
        raise BybitDepthError("invalid symbol")
    if received_time_ms <= 0:
        raise BybitDepthError("received_time_ms must be positive")
    snapshot = BybitDepthSnapshot(
        symbol=symbol,
        bids=_levels(payload.get("b"), side="bids"),
        asks=_levels(payload.get("a"), side="asks"),
        update_id=_positive_int(payload, "u"),
        sequence=_positive_int(payload, "seq"),
        source_time_ms=_positive_int(payload, "cts"),
        received_time_ms=received_time_ms,
    )
    if snapshot.bids[0].price >= snapshot.asks[0].price:
        raise BybitDepthError("crossed depth snapshot")
    return snapshot
