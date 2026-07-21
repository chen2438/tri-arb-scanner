"""Reconnectable Binance public diff-depth WebSocket shard."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from tri_arb.domain.models import PriceReference
from tri_arb.exchange.binance.depth import (
    BinanceDepthError,
    BinanceDepthEvent,
    BinanceDepthSnapshot,
    BinanceOrderBookState,
    normalize_depth_event,
)
from tri_arb.exchange.mexc.websocket import (
    DepthUpdate,
    OnDepth,
    OnStatus,
    WebSocketState,
    WebSocketStatus,
    _ignore_status,
)

MAX_SUBSCRIPTIONS_PER_CONNECTION = 30
MAX_BUFFERED_EVENTS_PER_SYMBOL = 10_000
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)

SnapshotLoader = Callable[[str], Awaitable[BinanceDepthSnapshot]]


@dataclass(frozen=True, slots=True)
class ReferenceUpdate:
    reference: PriceReference
    shard_id: int
    connection_generation: int
    subscription_generation: int


OnReference = Callable[[ReferenceUpdate], Awaitable[None]]


async def _ignore_reference(_update: ReferenceUpdate) -> None:
    return None


def stream_name(symbol: str) -> str:
    if not symbol or symbol != symbol.strip().upper() or len(symbol) > 64:
        raise ValueError("invalid Binance stream symbol")
    return f"{symbol.lower()}@depth@100ms"


def reference_stream_name(symbol: str) -> str:
    stream_name(symbol)
    return f"{symbol.lower()}@referencePrice"


def control_payload(operation: str, symbols: set[str], request_id: int) -> str:
    if operation not in {"SUBSCRIBE", "UNSUBSCRIBE"} or not symbols or request_id <= 0:
        raise ValueError("invalid Binance WebSocket control request")
    return json.dumps(
        {
            "method": operation,
            "params": [
                stream
                for symbol in sorted(symbols)
                for stream in (stream_name(symbol), reference_stream_name(symbol))
            ],
            "id": request_id,
        },
        separators=(",", ":"),
    )


def validate_control_message(message: Mapping[str, Any]) -> bool:
    if "code" in message:
        raise BinanceDepthError("Binance rejected WebSocket control request")
    if "result" not in message:
        return False
    request_id = message.get("id")
    if message.get("result") is not None or isinstance(request_id, bool) or not isinstance(
        request_id, int
    ):
        raise BinanceDepthError("invalid Binance WebSocket control response")
    return True


def normalize_reference_event(payload: Any, *, received_time_ms: int) -> PriceReference:
    if (
        not isinstance(payload, Mapping)
        or payload.get("e") != "referencePrice"
        or received_time_ms <= 0
    ):
        raise BinanceDepthError("invalid Binance reference-price event")
    symbol = payload.get("s")
    if not isinstance(symbol, str):
        raise BinanceDepthError("invalid Binance reference-price symbol")
    stream_name(symbol)
    raw_price = payload.get("r")
    if not isinstance(raw_price, str) or not raw_price or len(raw_price) > 128:
        raise BinanceDepthError("invalid Binance reference price")
    try:
        price = Decimal(raw_price)
    except InvalidOperation as error:
        raise BinanceDepthError("invalid Binance reference price") from error
    source_time = payload.get("t")
    if (
        not price.is_finite()
        or price <= 0
        or isinstance(source_time, bool)
        or not isinstance(source_time, int)
        or source_time <= 0
    ):
        raise BinanceDepthError("invalid Binance reference price")
    return PriceReference(symbol, price, 0, received_time_ms, source_time)


class BinanceDepthWebSocketShard:
    def __init__(
        self,
        url: str,
        shard_id: int,
        snapshot_loader: SnapshotLoader,
        on_depth: OnDepth,
        *,
        on_status: OnStatus = _ignore_status,
        on_reference: OnReference = _ignore_reference,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        sleep=asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if shard_id not in {0, 1}:
            raise ValueError("shard_id must be 0 or 1")
        self._url = url
        self._shard_id = shard_id
        self._snapshot_loader = snapshot_loader
        self._on_depth = on_depth
        self._on_status = on_status
        self._on_reference = on_reference
        self._now_ms = now_ms
        self._sleep = sleep
        self._jitter = jitter
        self._targets: tuple[str, ...] = ()
        self._connection_generation = 0
        self._subscription_generations: dict[str, int] = {}
        self._request_id = 0

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        normalized = tuple(sorted(set(symbols)))
        if len(normalized) != len(symbols) or len(normalized) > MAX_SUBSCRIPTIONS_PER_CONNECTION:
            raise ValueError("invalid Binance WebSocket target symbols")
        for symbol in normalized:
            stream_name(symbol)
        self._targets = normalized

    async def _status(self, state: WebSocketState, error: str | None = None) -> None:
        await self._on_status(
            WebSocketStatus(
                self._shard_id,
                state,
                self._connection_generation,
                self._targets,
                error,
            )
        )

    async def _control(self, websocket: Any, operation: str, symbols: set[str]) -> None:
        self._request_id += 1
        await websocket.send(control_payload(operation, symbols, self._request_id))

    async def _sync(
        self,
        websocket: Any,
        active: set[str],
        states: dict[str, BinanceOrderBookState],
        buffers: dict[str, list[BinanceDepthEvent]],
        snapshots: dict[str, asyncio.Task[BinanceDepthSnapshot]],
    ) -> bool:
        target = set(self._targets)
        removed, added = active - target, target - active
        if removed:
            await self._control(websocket, "UNSUBSCRIBE", removed)
            for symbol in removed:
                states.pop(symbol, None)
                buffers.pop(symbol, None)
                task = snapshots.pop(symbol, None)
                if task is not None:
                    task.cancel()
            active.difference_update(removed)
        if added:
            await self._control(websocket, "SUBSCRIBE", added)
            for symbol in added:
                buffers[symbol] = []
                snapshots[symbol] = asyncio.create_task(self._snapshot_loader(symbol))
                self._subscription_generations[symbol] = (
                    self._subscription_generations.get(symbol, 0) + 1
                )
            active.update(added)
        return bool(removed or added)

    async def _install_snapshots(
        self,
        active: set[str],
        states: dict[str, BinanceOrderBookState],
        buffers: dict[str, list[BinanceDepthEvent]],
        snapshots: dict[str, asyncio.Task[BinanceDepthSnapshot]],
    ) -> None:
        for symbol, task in tuple(snapshots.items()):
            if not task.done():
                continue
            snapshots.pop(symbol)
            snapshot = await task
            if symbol not in active:
                continue
            state = BinanceOrderBookState(snapshot)
            states[symbol] = state
            for event in buffers.pop(symbol, []):
                book = state.apply(event)
                if book is not None:
                    await self._on_depth(
                        DepthUpdate(
                            book,
                            self._shard_id,
                            self._connection_generation,
                            self._subscription_generations[symbol],
                        )
                    )

    async def _session(self, stop: asyncio.Event) -> None:
        self._connection_generation += 1
        await self._status(WebSocketState.CONNECTING)
        snapshots: dict[str, asyncio.Task[BinanceDepthSnapshot]] = {}
        try:
            async with connect(
                self._url,
                ping_interval=None,
                open_timeout=5,
                close_timeout=1,
                max_size=4 * 1024 * 1024,
            ) as websocket:
                active: set[str] = set()
                states: dict[str, BinanceOrderBookState] = {}
                buffers: dict[str, list[BinanceDepthEvent]] = {}
                await self._sync(websocket, active, states, buffers, snapshots)
                await self._status(WebSocketState.CONNECTED)
                while not stop.is_set() and self._targets:
                    if await self._sync(websocket, active, states, buffers, snapshots):
                        await self._status(WebSocketState.CONNECTED)
                    await self._install_snapshots(active, states, buffers, snapshots)
                    try:
                        raw_message = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                    except TimeoutError:
                        continue
                    if not isinstance(raw_message, str):
                        raise BinanceDepthError("Binance WebSocket message must be text")
                    try:
                        message = json.loads(raw_message)
                    except ValueError as error:
                        raise BinanceDepthError("invalid Binance WebSocket JSON") from error
                    if not isinstance(message, Mapping):
                        raise BinanceDepthError("Binance WebSocket message must be an object")
                    if validate_control_message(message):
                        continue
                    if message.get("e") == "referencePrice":
                        reference = normalize_reference_event(
                            message, received_time_ms=self._now_ms()
                        )
                        if reference.symbol not in active:
                            continue
                        await self._on_reference(
                            ReferenceUpdate(
                                reference,
                                self._shard_id,
                                self._connection_generation,
                                self._subscription_generations[reference.symbol],
                            )
                        )
                        continue
                    event = normalize_depth_event(message, received_time_ms=self._now_ms())
                    if event.symbol not in active:
                        continue
                    state = states.get(event.symbol)
                    if state is None:
                        buffer = buffers[event.symbol]
                        if len(buffer) >= MAX_BUFFERED_EVENTS_PER_SYMBOL:
                            raise BinanceDepthError("Binance depth buffer exceeded its limit")
                        buffer.append(event)
                        continue
                    book = state.apply(event)
                    if book is not None:
                        await self._on_depth(
                            DepthUpdate(
                                book,
                                self._shard_id,
                                self._connection_generation,
                                self._subscription_generations[event.symbol],
                            )
                        )
        finally:
            for task in snapshots.values():
                task.cancel()
            if snapshots:
                await asyncio.gather(*snapshots.values(), return_exceptions=True)

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0
        try:
            while not stop.is_set():
                if not self._targets:
                    await self._status(WebSocketState.IDLE)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.25)
                    except TimeoutError:
                        continue
                    break
                try:
                    await self._session(stop)
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except (ConnectionClosed, OSError, ValueError, WebSocketException) as error:
                    delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                    delay = min(30.0, delay * (0.8 + 0.4 * self._jitter()))
                    attempt += 1
                    await self._status(WebSocketState.BACKOFF, f"{type(error).__name__}: {error}")
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except TimeoutError:
                        continue
        finally:
            await self._status(WebSocketState.STOPPED)
