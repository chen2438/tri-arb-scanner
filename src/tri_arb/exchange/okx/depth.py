"""Fail-closed OKX incremental order-book reconstruction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import BookLevel, OrderBook

DEPTH_CHANNEL = "books"
DEPTH_LEVELS = 20
MAX_BOOK_LEVELS = 400
MAX_DECIMAL_LENGTH = 128


class OkxDepthError(ValueError):
    pass


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or value != value.strip().upper():
        raise OkxDepthError(f"invalid {field}")
    return value


def _integer(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OkxDepthError(f"invalid {field}")
    try:
        result = int(value)
    except ValueError as error:
        raise OkxDepthError(f"invalid {field}") from error
    if str(result) != str(value) or result < minimum:
        raise OkxDepthError(f"invalid {field}")
    return result


def _levels(raw: Any, side: str) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) > MAX_BOOK_LEVELS:
        raise OkxDepthError(f"invalid {side} levels")
    result: list[tuple[Decimal, Decimal]] = []
    seen: set[Decimal] = set()
    for item in raw:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) < 2:
            raise OkxDepthError(f"invalid {side} level")
        price_raw, quantity_raw = item[0], item[1]
        if (
            not isinstance(price_raw, str)
            or not isinstance(quantity_raw, str)
            or len(price_raw) > MAX_DECIMAL_LENGTH
            or len(quantity_raw) > MAX_DECIMAL_LENGTH
        ):
            raise OkxDepthError(f"invalid {side} level")
        try:
            price, quantity = Decimal(price_raw), Decimal(quantity_raw)
        except InvalidOperation as error:
            raise OkxDepthError(f"invalid {side} level") from error
        if (
            not price.is_finite()
            or not quantity.is_finite()
            or price <= 0
            or quantity < 0
            or abs(price.adjusted()) > 60
            or (quantity and abs(quantity.adjusted()) > 60)
            or price in seen
        ):
            raise OkxDepthError(f"invalid {side} level")
        seen.add(price)
        result.append((price, quantity))
    return tuple(result)


class OkxOrderBookState:
    def __init__(self, symbol: str) -> None:
        self.symbol = _identity(symbol, "symbol")
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._seq_id: int | None = None

    @property
    def seq_id(self) -> int | None:
        return self._seq_id

    def reset(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self._seq_id = None

    @staticmethod
    def _merge(target: dict[Decimal, Decimal], levels: tuple[tuple[Decimal, Decimal], ...]) -> None:
        for price, quantity in levels:
            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = quantity
        if len(target) > MAX_BOOK_LEVELS:
            raise OkxDepthError("reconstructed book exceeds 400 levels")

    def apply(self, message: Any, *, received_time_ms: int) -> OrderBook:
        if received_time_ms <= 0 or not isinstance(message, Mapping):
            raise OkxDepthError("invalid depth message")
        arg = message.get("arg")
        if not isinstance(arg, Mapping):
            raise OkxDepthError("depth message is missing arg")
        if (
            arg.get("channel") != DEPTH_CHANNEL
            or _identity(arg.get("instId"), "instId") != self.symbol
        ):
            raise OkxDepthError("depth channel or symbol mismatch")
        action = message.get("action")
        if action not in {"snapshot", "update"}:
            raise OkxDepthError("invalid depth action")
        data = message.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
            raise OkxDepthError("depth message must contain one data object")
        raw = data[0]
        seq_id = _integer(raw.get("seqId"), "seqId", minimum=0)
        prev_seq_id = _integer(raw.get("prevSeqId"), "prevSeqId", minimum=-1)
        source_time_ms = _integer(raw.get("ts"), "ts", minimum=1)
        bids = _levels(raw.get("bids"), "bid")
        asks = _levels(raw.get("asks"), "ask")
        if action == "snapshot":
            if prev_seq_id != -1:
                raise OkxDepthError("snapshot prevSeqId must be -1")
            self.reset()
        else:
            if self._seq_id is None:
                raise OkxDepthError("incremental update arrived before snapshot")
            if prev_seq_id != self._seq_id or seq_id < prev_seq_id:
                raise OkxDepthError("order-book sequence discontinuity")
            if seq_id == prev_seq_id and (bids or asks):
                raise OkxDepthError("unchanged sequence cannot mutate the book")
        self._merge(self._bids, bids)
        self._merge(self._asks, asks)
        self._seq_id = seq_id
        best_bids = tuple(
            BookLevel(price, self._bids[price])
            for price in sorted(self._bids, reverse=True)[:DEPTH_LEVELS]
        )
        best_asks = tuple(
            BookLevel(price, self._asks[price]) for price in sorted(self._asks)[:DEPTH_LEVELS]
        )
        if not best_bids or not best_asks or best_bids[0].price >= best_asks[0].price:
            raise OkxDepthError("reconstructed order book is empty or crossed")
        return OrderBook(
            symbol=self.symbol,
            bids=best_bids,
            asks=best_asks,
            version=str(seq_id),
            source_time_ms=source_time_ms,
            received_time_ms=received_time_ms,
        )
