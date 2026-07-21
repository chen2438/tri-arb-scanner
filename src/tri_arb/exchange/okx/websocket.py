"""Reconnectable OKX public incremental-depth WebSocket shard."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Callable, Mapping
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from tri_arb.exchange.mexc.websocket import (
    DepthUpdate,
    OnDepth,
    OnStatus,
    WebSocketState,
    WebSocketStatus,
    _ignore_status,
)
from tri_arb.exchange.okx.depth import DEPTH_CHANNEL, OkxDepthError, OkxOrderBookState

MAX_SUBSCRIPTIONS_PER_CONNECTION = 30
PING_INTERVAL_SECONDS = 20.0
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


def control_payload(operation: str, symbols: set[str]) -> str:
    if operation not in {"subscribe", "unsubscribe"} or not symbols:
        raise ValueError("invalid OKX WebSocket control request")
    return json.dumps(
        {
            "op": operation,
            "args": [
                {"channel": DEPTH_CHANNEL, "instId": symbol} for symbol in sorted(symbols)
            ],
        },
        separators=(",", ":"),
    )


def validate_control_message(message: Mapping[str, Any]) -> bool:
    event = message.get("event")
    if event == "error":
        raise ValueError("OKX rejected WebSocket control request")
    if event in {"subscribe", "unsubscribe"}:
        arg = message.get("arg")
        if not isinstance(arg, Mapping) or arg.get("channel") != DEPTH_CHANNEL:
            raise ValueError("invalid OKX WebSocket control response")
        return True
    return False


def depth_message_symbol(message: Mapping[str, Any]) -> str:
    """Return the instrument for a well-formed public books message."""
    arg = message.get("arg")
    if not isinstance(arg, Mapping) or arg.get("channel") != DEPTH_CHANNEL:
        raise OkxDepthError("invalid OKX depth channel")
    symbol = arg.get("instId")
    if not isinstance(symbol, str) or not symbol:
        raise OkxDepthError("invalid OKX depth symbol")
    return symbol


class OkxDepthWebSocketShard:
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
        self._states: dict[str, OkxOrderBookState] = {}

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        normalized = tuple(sorted(set(symbols)))
        if len(normalized) != len(symbols) or len(normalized) > MAX_SUBSCRIPTIONS_PER_CONNECTION:
            raise ValueError("invalid OKX WebSocket target symbols")
        for symbol in normalized:
            OkxOrderBookState(symbol)
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

    async def _sync(self, websocket: Any, active: set[str]) -> bool:
        target = set(self._targets)
        removed, added = active - target, target - active
        if removed:
            await websocket.send(control_payload("unsubscribe", removed))
            for symbol in removed:
                self._states.pop(symbol, None)
            active.difference_update(removed)
        if added:
            await websocket.send(control_payload("subscribe", added))
            for symbol in added:
                self._states[symbol] = OkxOrderBookState(symbol)
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
                    await websocket.send("ping")
                    last_ping = self._monotonic()
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=0.25)
                except TimeoutError:
                    continue
                if not isinstance(raw_message, str):
                    raise OkxDepthError("OKX WebSocket message must be text")
                if raw_message == "pong":
                    continue
                try:
                    message = json.loads(raw_message)
                except ValueError as error:
                    raise OkxDepthError("invalid OKX WebSocket JSON") from error
                if not isinstance(message, Mapping):
                    raise OkxDepthError("OKX WebSocket message must be an object")
                if validate_control_message(message):
                    continue
                symbol = depth_message_symbol(message)
                if symbol not in active:
                    # An update already queued by OKX can arrive after its
                    # unsubscribe acknowledgement. It belongs to a retired
                    # local state and must not tear down the whole shard.
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
                    await self._status(WebSocketState.BACKOFF, f"{type(error).__name__}: {error}")
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except TimeoutError:
                        continue
        finally:
            self._states.clear()
            await self._status(WebSocketState.STOPPED)
