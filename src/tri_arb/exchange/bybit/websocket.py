"""Reconnectable Bybit public spot orderbook shard."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Callable, Mapping
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from tri_arb.exchange.bybit.depth import (
    STREAM_DEPTH_LEVELS,
    BybitDepthError,
    BybitOrderBookState,
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
MAX_TOPICS_PER_REQUEST = 10
PING_INTERVAL_SECONDS = 20.0
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


def topic(symbol: str) -> str:
    BybitOrderBookState(symbol)
    return f"orderbook.{STREAM_DEPTH_LEVELS}.{symbol}"


def control_payload(operation: str, symbols: set[str], request_id: str) -> str:
    if (
        operation not in {"subscribe", "unsubscribe"}
        or not symbols
        or len(symbols) > MAX_TOPICS_PER_REQUEST
        or not request_id
    ):
        raise ValueError("invalid Bybit WebSocket control request")
    return json.dumps(
        {
            "req_id": request_id,
            "op": operation,
            "args": [topic(symbol) for symbol in sorted(symbols)],
        },
        separators=(",", ":"),
    )


def validate_control_message(message: Mapping[str, Any]) -> bool:
    operation = message.get("op")
    if operation in {"subscribe", "unsubscribe"}:
        if message.get("success") is not True or message.get("ret_msg") not in {"", operation}:
            raise BybitDepthError("Bybit rejected WebSocket control request")
        return True
    if operation in {"ping", "pong"}:
        if message.get("success") is False:
            raise BybitDepthError("Bybit rejected WebSocket heartbeat")
        return True
    return False


def depth_message_symbol(message: Mapping[str, Any]) -> str:
    value = message.get("topic")
    prefix = f"orderbook.{STREAM_DEPTH_LEVELS}."
    if not isinstance(value, str) or not value.startswith(prefix):
        raise BybitDepthError("invalid Bybit depth topic")
    symbol = value[len(prefix):]
    BybitOrderBookState(symbol)
    return symbol


def _chunks(symbols: set[str]) -> tuple[set[str], ...]:
    ordered = sorted(symbols)
    return tuple(
        set(ordered[index:index + MAX_TOPICS_PER_REQUEST])
        for index in range(0, len(ordered), MAX_TOPICS_PER_REQUEST)
    )


class BybitDepthWebSocketShard:
    def __init__(
        self,
        url: str,
        shard_id: int,
        on_depth: OnDepth,
        *,
        on_status: OnStatus = _ignore_status,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        monotonic: Callable[[], float] = time.monotonic,
        sleep=asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if shard_id not in {0, 1}:
            raise ValueError("shard_id must be 0 or 1")
        self._url = url
        self._shard_id = shard_id
        self._on_depth = on_depth
        self._on_status = on_status
        self._now_ms = now_ms
        self._monotonic = monotonic
        self._sleep = sleep
        self._jitter = jitter
        self._targets: tuple[str, ...] = ()
        self._connection_generation = 0
        self._subscription_generations: dict[str, int] = {}
        self._states: dict[str, BybitOrderBookState] = {}
        self._request_id = 0

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        normalized = tuple(sorted(set(symbols)))
        if len(normalized) != len(symbols) or len(normalized) > MAX_SUBSCRIPTIONS_PER_CONNECTION:
            raise ValueError("invalid Bybit WebSocket target symbols")
        for symbol in normalized:
            BybitOrderBookState(symbol)
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

    async def _send_control(self, websocket: Any, operation: str, symbols: set[str]) -> None:
        for chunk in _chunks(symbols):
            self._request_id += 1
            await websocket.send(
                control_payload(operation, chunk, f"tri-arb-{self._request_id}")
            )

    async def _sync(self, websocket: Any, active: set[str]) -> bool:
        target = set(self._targets)
        removed, added = active - target, target - active
        if removed:
            await self._send_control(websocket, "unsubscribe", removed)
            for symbol in removed:
                self._states.pop(symbol, None)
            active.difference_update(removed)
        if added:
            await self._send_control(websocket, "subscribe", added)
            for symbol in added:
                self._states[symbol] = BybitOrderBookState(symbol)
                self._subscription_generations[symbol] = (
                    self._subscription_generations.get(symbol, 0) + 1
                )
            active.update(added)
        return bool(removed or added)

    async def _session(self, stop: asyncio.Event) -> None:
        self._connection_generation += 1
        self._states.clear()
        await self._status(WebSocketState.CONNECTING)
        async with connect(
            self._url,
            ping_interval=None,
            open_timeout=5,
            close_timeout=1,
            max_size=4 * 1024 * 1024,
        ) as websocket:
            active: set[str] = set()
            await self._sync(websocket, active)
            await self._status(WebSocketState.CONNECTED)
            last_ping = self._monotonic()
            while not stop.is_set() and self._targets:
                if await self._sync(websocket, active):
                    await self._status(WebSocketState.CONNECTED)
                if self._monotonic() - last_ping >= PING_INTERVAL_SECONDS:
                    self._request_id += 1
                    await websocket.send(
                        json.dumps(
                            {"req_id": f"tri-arb-{self._request_id}", "op": "ping"},
                            separators=(",", ":"),
                        )
                    )
                    last_ping = self._monotonic()
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=0.25)
                except TimeoutError:
                    continue
                if not isinstance(raw_message, str):
                    raise BybitDepthError("Bybit WebSocket message must be text")
                try:
                    message = json.loads(raw_message)
                except ValueError as error:
                    raise BybitDepthError("invalid Bybit WebSocket JSON") from error
                if not isinstance(message, Mapping):
                    raise BybitDepthError("Bybit WebSocket message must be an object")
                if validate_control_message(message):
                    continue
                symbol = depth_message_symbol(message)
                if symbol not in active:
                    continue
                book = self._states[symbol].apply(message, received_time_ms=self._now_ms())
                await self._on_depth(
                    DepthUpdate(
                        book,
                        self._shard_id,
                        self._connection_generation,
                        self._subscription_generations[symbol],
                    )
                )

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
                    await self._status(
                        WebSocketState.BACKOFF, f"{type(error).__name__}: {error}"
                    )
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except TimeoutError:
                        continue
        finally:
            self._states.clear()
            await self._status(WebSocketState.STOPPED)
