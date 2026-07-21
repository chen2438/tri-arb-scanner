"""Reconnectable MEXC public partial-depth WebSocket shard."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from tri_arb.domain.models import OrderBook
from tri_arb.exchange.mexc.depth import decode_depth_frame, depth_channel
from tri_arb.exchange.mexc.subscriptions import MAX_SUBSCRIPTIONS_PER_CONNECTION

PING_INTERVAL_SECONDS = 20.0
ROTATE_AFTER_SECONDS = 23 * 60 * 60 + 50 * 60
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


class WebSocketState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BACKOFF = "backoff"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class DepthUpdate:
    book: OrderBook
    shard_id: int
    connection_generation: int
    subscription_generation: int


@dataclass(frozen=True, slots=True)
class WebSocketStatus:
    shard_id: int
    state: WebSocketState
    connection_generation: int
    subscriptions: tuple[str, ...]
    error: str | None = None


OnDepth = Callable[[DepthUpdate], Awaitable[None]]
OnStatus = Callable[[WebSocketStatus], Awaitable[None]]
NowMs = Callable[[], int]
Monotonic = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


async def _ignore_status(_status: WebSocketStatus) -> None:
    return None


def _control_payload(method: str, symbols: set[str]) -> str:
    if method not in {"SUBSCRIPTION", "UNSUBSCRIPTION"}:
        raise ValueError("invalid WebSocket control method")
    return json.dumps(
        {"method": method, "params": [depth_channel(symbol) for symbol in sorted(symbols)]},
        separators=(",", ":"),
    )


def validate_control_response(message: str) -> None:
    try:
        payload = json.loads(message)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid WebSocket control JSON") from error
    if not isinstance(payload, Mapping):
        raise ValueError("WebSocket control response must be an object")
    code = payload.get("code")
    response_id = payload.get("id")
    message_text = payload.get("msg")
    if isinstance(code, bool) or code != 0 or isinstance(response_id, bool) or response_id != 0:
        raise ValueError("MEXC rejected WebSocket control request")
    if not isinstance(message_text, str) or not message_text:
        raise ValueError("invalid WebSocket control response message")


class MexcDepthWebSocketShard:
    """One connection with at most 30 dynamically reconciled depth channels."""

    def __init__(
        self,
        url: str,
        shard_id: int,
        on_depth: OnDepth,
        *,
        on_status: OnStatus = _ignore_status,
        now_ms: NowMs = lambda: time.time_ns() // 1_000_000,
        monotonic: Monotonic = time.monotonic,
        sleep: Sleep = asyncio.sleep,
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

    @property
    def targets(self) -> tuple[str, ...]:
        return self._targets

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        normalized = tuple(sorted(set(symbols)))
        if len(normalized) != len(symbols):
            raise ValueError("WebSocket target symbols must be unique")
        if len(normalized) > MAX_SUBSCRIPTIONS_PER_CONNECTION:
            raise ValueError("WebSocket shard cannot exceed 30 subscriptions")
        for symbol in normalized:
            depth_channel(symbol)
        self._targets = normalized

    async def _status(self, state: WebSocketState, error: str | None = None) -> None:
        await self._on_status(
            WebSocketStatus(
                shard_id=self._shard_id,
                state=state,
                connection_generation=self._connection_generation,
                subscriptions=self._targets,
                error=error,
            )
        )

    async def _sync(self, websocket: Any, active: set[str]) -> None:
        target = set(self._targets)
        removed = active - target
        added = target - active
        if removed:
            await websocket.send(_control_payload("UNSUBSCRIPTION", removed))
            active.difference_update(removed)
        if added:
            await websocket.send(_control_payload("SUBSCRIPTION", added))
            for symbol in added:
                self._subscription_generations[symbol] = (
                    self._subscription_generations.get(symbol, 0) + 1
                )
            active.update(added)

    async def _session(self, stop: asyncio.Event) -> None:
        self._connection_generation += 1
        await self._status(WebSocketState.CONNECTING)
        async with connect(
            self._url,
            ping_interval=None,
            open_timeout=3,
            close_timeout=1,
            max_size=2 * 1024 * 1024,
        ) as websocket:
            active: set[str] = set()
            await self._sync(websocket, active)
            await self._status(WebSocketState.CONNECTED)
            connected_at = last_ping = self._monotonic()
            while not stop.is_set() and self._targets:
                await self._sync(websocket, active)
                now = self._monotonic()
                if now - connected_at >= ROTATE_AFTER_SECONDS:
                    return
                if now - last_ping >= PING_INTERVAL_SECONDS:
                    await websocket.send('{"method":"PING"}')
                    last_ping = now
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=0.25)
                except TimeoutError:
                    continue
                if isinstance(message, str):
                    validate_control_response(message)
                    continue
                book = decode_depth_frame(message, received_time_ms=self._now_ms())
                if book.symbol not in active:
                    continue
                await self._on_depth(
                    DepthUpdate(
                        book=book,
                        shard_id=self._shard_id,
                        connection_generation=self._connection_generation,
                        subscription_generation=self._subscription_generations[book.symbol],
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
                    sleep_task = asyncio.create_task(self._sleep(delay))
                    stop_task = asyncio.create_task(stop.wait())
                    done, pending = await asyncio.wait(
                        {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    await asyncio.gather(*done)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await self._status(WebSocketState.STOPPED)
