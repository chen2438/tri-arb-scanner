"""Bybit V5 public REST depth snapshot normalization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import BookLevel, OrderBook

MAX_DEPTH_LEVELS = 1_000
STREAM_DEPTH_LEVELS = 200
OUTPUT_DEPTH_LEVELS = 20
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


def _updates(raw: Any, *, side: str) -> tuple[tuple[Decimal, Decimal], ...]:
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or len(raw) > STREAM_DEPTH_LEVELS
    ):
        raise BybitDepthError(f"invalid {side} updates")
    updates: list[tuple[Decimal, Decimal]] = []
    seen: set[Decimal] = set()
    for index, item in enumerate(raw):
        if (
            not isinstance(item, Sequence)
            or isinstance(item, (str, bytes))
            or len(item) != 2
        ):
            raise BybitDepthError(f"invalid {side} update at index {index}")
        price_raw, quantity_raw = item
        if not all(
            isinstance(value, str) and 0 < len(value) <= MAX_DECIMAL_LENGTH
            for value in item
        ):
            raise BybitDepthError(f"invalid {side} update at index {index}")
        try:
            price, quantity = Decimal(price_raw), Decimal(quantity_raw)
        except InvalidOperation as error:
            raise BybitDepthError(f"invalid {side} update at index {index}") from error
        if (
            not price.is_finite()
            or not quantity.is_finite()
            or price <= 0
            or quantity < 0
            or price in seen
        ):
            raise BybitDepthError(f"invalid {side} update at index {index}")
        seen.add(price)
        updates.append((price, quantity))
    return tuple(updates)


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


class BybitOrderBookState:
    """Reconstruct one Bybit orderbook.200 stream and reject stale updates."""

    def __init__(self, symbol: str) -> None:
        if not symbol or symbol != symbol.strip().upper() or len(symbol) > 64:
            raise BybitDepthError("invalid symbol")
        self.symbol = symbol
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._update_id: int | None = None
        self._sequence: int | None = None

    def reset(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self._update_id = None
        self._sequence = None

    @staticmethod
    def _merge(
        target: dict[Decimal, Decimal], updates: tuple[tuple[Decimal, Decimal], ...]
    ) -> None:
        for price, quantity in updates:
            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = quantity
        if len(target) > STREAM_DEPTH_LEVELS:
            raise BybitDepthError("reconstructed book exceeds 200 levels")

    def apply(self, message: Any, *, received_time_ms: int) -> OrderBook:
        if not isinstance(message, Mapping) or received_time_ms <= 0:
            raise BybitDepthError("invalid depth message")
        if message.get("topic") != f"orderbook.{STREAM_DEPTH_LEVELS}.{self.symbol}":
            raise BybitDepthError("depth topic mismatch")
        action = message.get("type")
        if action not in {"snapshot", "delta"}:
            raise BybitDepthError("invalid depth type")
        data = message.get("data")
        if not isinstance(data, Mapping) or data.get("s") != self.symbol:
            raise BybitDepthError("depth symbol mismatch")
        update_id = _positive_int(data, "u")
        sequence = _positive_int(data, "seq")
        source_time_ms = _positive_int(message, "cts")
        bids = _updates(data.get("b"), side="bid")
        asks = _updates(data.get("a"), side="ask")
        if action == "snapshot":
            self.reset()
        else:
            if self._update_id is None or self._sequence is None:
                raise BybitDepthError("delta arrived before snapshot")
            if update_id <= self._update_id or sequence <= self._sequence:
                raise BybitDepthError("out-of-order depth delta")
            if update_id == 1:
                raise BybitDepthError("service restart delta requires snapshot")
        self._merge(self._bids, bids)
        self._merge(self._asks, asks)
        self._update_id = update_id
        self._sequence = sequence
        best_bids = tuple(
            BookLevel(price, self._bids[price])
            for price in sorted(self._bids, reverse=True)[:OUTPUT_DEPTH_LEVELS]
        )
        best_asks = tuple(
            BookLevel(price, self._asks[price])
            for price in sorted(self._asks)[:OUTPUT_DEPTH_LEVELS]
        )
        if not best_bids or not best_asks or best_bids[0].price >= best_asks[0].price:
            raise BybitDepthError("reconstructed order book is empty or crossed")
        return OrderBook(
            symbol=self.symbol,
            bids=best_bids,
            asks=best_asks,
            version=f"{update_id}:{sequence}",
            source_time_ms=source_time_ms,
            received_time_ms=received_time_ms,
        )
