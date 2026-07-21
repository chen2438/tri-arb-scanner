"""Bounded, public-only REST client for OKX spot instruments and tickers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from tri_arb.domain.models import BookTicker, MarketActivity
from tri_arb.exchange.mexc.rest import ServerClock
from tri_arb.exchange.okx.metadata import NormalizedOkxInstruments, normalize_instruments

MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_TICKERS = 10_000
MAX_DECIMAL_LENGTH = 128
DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)


class OkxRestError(RuntimeError):
    pass


class OkxRestProtocolError(OkxRestError):
    pass


@dataclass(frozen=True, slots=True)
class OkxTickerRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedOkxTickers:
    tickers: tuple[BookTicker, ...]
    activities: tuple[MarketActivity, ...]
    rejections: tuple[OkxTickerRejection, ...]


def _positive(raw: Mapping[str, Any], field: str) -> Decimal:
    value = raw.get(field)
    if not isinstance(value, str) or not value or len(value) > MAX_DECIMAL_LENGTH:
        raise ValueError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid {field}") from error
    if not result.is_finite() or result <= 0 or abs(result.adjusted()) > 60:
        raise ValueError(f"invalid {field}")
    return result


def _non_negative(raw: Mapping[str, Any], field: str) -> Decimal:
    value = raw.get(field)
    if not isinstance(value, str) or not value or len(value) > MAX_DECIMAL_LENGTH:
        raise ValueError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid {field}") from error
    if not result.is_finite() or result < 0 or (result and abs(result.adjusted()) > 60):
        raise ValueError(f"invalid {field}")
    return result


def normalize_tickers(payload: Any, *, received_time_ms: int) -> NormalizedOkxTickers:
    if not isinstance(payload, list) or len(payload) > MAX_TICKERS:
        raise OkxRestProtocolError("OKX tickers response must be a bounded list")
    if received_time_ms <= 0:
        raise ValueError("received_time_ms must be positive")
    tickers: list[BookTicker] = []
    activities: list[MarketActivity] = []
    rejections: list[OkxTickerRejection] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, Mapping):
            raise OkxRestProtocolError(f"OKX ticker at index {index} must be an object")
        symbol = raw.get("instId")
        if isinstance(symbol, str) and len(symbol) <= 64:
            if symbol in seen:
                raise OkxRestProtocolError(f"duplicate OKX ticker: {symbol}")
            seen.add(symbol)
        try:
            if raw.get("instType") != "SPOT" or not isinstance(symbol, str):
                raise ValueError("invalid spot ticker identity")
            ticker = BookTicker(
                symbol=symbol,
                bid_price=_positive(raw, "bidPx"),
                bid_quantity=_positive(raw, "bidSz"),
                ask_price=_positive(raw, "askPx"),
                ask_quantity=_positive(raw, "askSz"),
                received_time_ms=received_time_ms,
            )
            activity = MarketActivity(
                symbol=symbol,
                quote_volume=_non_negative(raw, "volCcy24h"),
                received_time_ms=received_time_ms,
            )
        except ValueError as error:
            rejections.append(OkxTickerRejection(str(symbol or f"index {index}")[:64], str(error)))
            continue
        tickers.append(ticker)
        activities.append(activity)
    return NormalizedOkxTickers(
        tuple(sorted(tickers, key=lambda item: item.symbol)),
        tuple(sorted(activities, key=lambda item: item.symbol)),
        tuple(sorted(rejections, key=lambda item: item.symbol)),
    )


Sleep = Callable[[float], Awaitable[None]]
NowMs = Callable[[], int]


class OkxRestClient:
    def __init__(
        self,
        base_url: str,
        *,
        taker_commission: Decimal,
        client: httpx.AsyncClient | None = None,
        retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
        sleep: Sleep = asyncio.sleep,
        now_ms: NowMs = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=5.0)
        self._owns_client = client is None
        self._fee = taker_commission
        self._retry_delays = tuple(retry_delays)
        self._sleep = sleep
        self._now_ms = now_ms
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, path: str, params: Mapping[str, str] | None = None) -> list[Any]:
        for attempt in range(len(self._retry_delays) + 1):
            try:
                response = await self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == len(self._retry_delays):
                    raise OkxRestError(f"GET {path} failed after retries") from error
                await self._sleep(self._retry_delays[attempt])
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == len(self._retry_delays):
                    raise OkxRestError(f"GET {path} failed with {response.status_code}")
                await self._sleep(self._retry_delays[attempt])
                continue
            if response.status_code >= 400:
                raise OkxRestError(f"GET {path} failed with {response.status_code}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise OkxRestProtocolError("OKX response exceeds byte limit")
            try:
                payload = response.json()
            except ValueError as error:
                raise OkxRestProtocolError("OKX response is not JSON") from error
            if not isinstance(payload, Mapping) or payload.get("code") != "0":
                raise OkxRestProtocolError("OKX response envelope is unsuccessful")
            data = payload.get("data")
            if not isinstance(data, list):
                raise OkxRestProtocolError("OKX response data must be a list")
            return data
        raise AssertionError("unreachable")

    async def instruments(self) -> NormalizedOkxInstruments:
        data = await self._request("/api/v5/public/instruments", {"instType": "SPOT"})
        return normalize_instruments(data, taker_commission=self._fee)

    async def tickers(self) -> NormalizedOkxTickers:
        async with self._lock:
            data = await self._request("/api/v5/market/tickers", {"instType": "SPOT"})
            return normalize_tickers(data, received_time_ms=self._now_ms())

    async def calibrate_clock(self) -> ServerClock:
        started = self._now_ms()
        data = await self._request("/api/v5/public/time")
        finished = self._now_ms()
        if len(data) != 1 or not isinstance(data[0], Mapping):
            raise OkxRestProtocolError("OKX time response must contain one object")
        try:
            server_ms = int(data[0]["ts"])
        except (KeyError, TypeError, ValueError) as error:
            raise OkxRestProtocolError("invalid OKX server time") from error
        if server_ms <= 0:
            raise OkxRestProtocolError("invalid OKX server time")
        return ServerClock(server_ms - (started + finished) // 2, finished - started, finished)
