"""Sequence-safe Binance Spot depth snapshot and diff reconstruction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import BookLevel, OrderBook

MAX_LEVELS = 5_000
OUTPUT_LEVELS = 20
MAX_DECIMAL_LENGTH = 128


class BinanceDepthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BinanceDepthSnapshot:
    symbol: str
    last_update_id: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    received_time_ms: int


@dataclass(frozen=True, slots=True)
class BinanceDepthEvent:
    symbol: str
    first_update_id: int
    final_update_id: int
    source_time_ms: int
    received_time_ms: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip().upper() or len(value) > 64:
        raise BinanceDepthError("invalid depth symbol")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BinanceDepthError(f"invalid {field}")
    return value


def _decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > MAX_DECIMAL_LENGTH:
        raise BinanceDepthError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise BinanceDepthError(f"invalid {field}") from error
    if (
        not result.is_finite()
        or result < 0
        or (result == 0 and not allow_zero)
        or (result and abs(result.adjusted()) > 60)
    ):
        raise BinanceDepthError(f"invalid {field}")
    return result


def _levels(payload: Any, side: str, *, allow_zero: bool) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(payload, list) or len(payload) > MAX_LEVELS:
        raise BinanceDepthError(f"invalid {side} levels")
    values: list[tuple[Decimal, Decimal]] = []
    seen: set[Decimal] = set()
    for raw in payload:
        if not isinstance(raw, list) or len(raw) != 2:
            raise BinanceDepthError(f"invalid {side} level")
        price = _decimal(raw[0], f"{side} price")
        quantity = _decimal(raw[1], f"{side} quantity", allow_zero=allow_zero)
        if price in seen:
            raise BinanceDepthError(f"duplicate {side} price")
        seen.add(price)
        values.append((price, quantity))
    return tuple(values)


def normalize_depth_snapshot(
    payload: Any, *, symbol: str, received_time_ms: int
) -> BinanceDepthSnapshot:
    if not isinstance(payload, Mapping) or received_time_ms <= 0:
        raise BinanceDepthError("invalid depth snapshot")
    normalized_symbol = _identity(symbol)
    bids = _levels(payload.get("bids"), "bid", allow_zero=False)
    asks = _levels(payload.get("asks"), "ask", allow_zero=False)
    if not bids or not asks:
        raise BinanceDepthError("depth snapshot cannot be empty")
    if any(bids[index][0] <= bids[index + 1][0] for index in range(len(bids) - 1)):
        raise BinanceDepthError("snapshot bids are not descending")
    if any(asks[index][0] >= asks[index + 1][0] for index in range(len(asks) - 1)):
        raise BinanceDepthError("snapshot asks are not ascending")
    if bids[0][0] >= asks[0][0]:
        raise BinanceDepthError("depth snapshot is crossed")
    return BinanceDepthSnapshot(
        normalized_symbol,
        _integer(payload.get("lastUpdateId"), "lastUpdateId"),
        tuple(BookLevel(*level) for level in bids),
        tuple(BookLevel(*level) for level in asks),
        received_time_ms,
    )


def normalize_depth_event(payload: Any, *, received_time_ms: int) -> BinanceDepthEvent:
    if (
        not isinstance(payload, Mapping)
        or payload.get("e") != "depthUpdate"
        or received_time_ms <= 0
    ):
        raise BinanceDepthError("invalid depth event")
    first = _integer(payload.get("U"), "first update ID")
    final = _integer(payload.get("u"), "final update ID")
    if first > final:
        raise BinanceDepthError("depth event update range is reversed")
    return BinanceDepthEvent(
        _identity(payload.get("s")),
        first,
        final,
        _integer(payload.get("E"), "event time"),
        received_time_ms,
        _levels(payload.get("b"), "bid", allow_zero=True),
        _levels(payload.get("a"), "ask", allow_zero=True),
    )


class BinanceOrderBookState:
    def __init__(self, snapshot: BinanceDepthSnapshot) -> None:
        self.symbol = snapshot.symbol
        self._last_update_id = snapshot.last_update_id
        self._bids = {level.price: level.quantity for level in snapshot.bids}
        self._asks = {level.price: level.quantity for level in snapshot.asks}

    @property
    def last_update_id(self) -> int:
        return self._last_update_id

    def apply(self, event: BinanceDepthEvent) -> OrderBook | None:
        if event.symbol != self.symbol:
            raise BinanceDepthError("depth event symbol mismatch")
        if event.final_update_id <= self._last_update_id:
            return None
        expected = self._last_update_id + 1
        if event.first_update_id > expected or event.final_update_id < expected:
            raise BinanceDepthError("depth event sequence discontinuity")
        for side, changes in ((self._bids, event.bids), (self._asks, event.asks)):
            for price, quantity in changes:
                if quantity == 0:
                    side.pop(price, None)
                else:
                    side[price] = quantity
        bids = sorted(self._bids.items(), reverse=True)[:OUTPUT_LEVELS]
        asks = sorted(self._asks.items())[:OUTPUT_LEVELS]
        if not bids or not asks or bids[0][0] >= asks[0][0]:
            raise BinanceDepthError("reconstructed depth is empty or crossed")
        self._last_update_id = event.final_update_id
        return OrderBook(
            symbol=self.symbol,
            bids=tuple(BookLevel(*level) for level in bids),
            asks=tuple(BookLevel(*level) for level in asks),
            version=str(event.final_update_id),
            source_time_ms=event.source_time_ms,
            received_time_ms=event.received_time_ms,
        )
