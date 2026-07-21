"""Bounded public-only Bybit V5 spot REST client."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from tri_arb.domain.models import BookTicker, MarketActivity, PriceLimit
from tri_arb.exchange.bybit.depth import BybitDepthSnapshot, normalize_depth_snapshot
from tri_arb.exchange.bybit.metadata import NormalizedBybitInstruments, normalize_instruments
from tri_arb.exchange.mexc.rest import ServerClock

MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_TICKERS = 10_000
MAX_DECIMAL_LENGTH = 128
DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)


class BybitRestError(RuntimeError):
    pass


class BybitRestProtocolError(BybitRestError):
    pass


@dataclass(frozen=True, slots=True)
class BybitTickerRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedBybitTickers:
    tickers: tuple[BookTicker, ...]
    activities: tuple[MarketActivity, ...]
    rejections: tuple[BybitTickerRejection, ...]


def _decimal(raw: Mapping[str, Any], field: str, *, allow_zero: bool = False) -> Decimal:
    value = raw.get(field)
    if not isinstance(value, str) or not value or len(value) > MAX_DECIMAL_LENGTH:
        raise ValueError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid {field}") from error
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise ValueError(f"invalid {field}")
    return result


def _symbol(raw: Mapping[str, Any]) -> str:
    value = raw.get("symbol")
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip().upper()
        or len(value) > 64
    ):
        raise ValueError("invalid symbol")
    return value


def normalize_tickers(payload: Any, *, received_time_ms: int) -> NormalizedBybitTickers:
    if not isinstance(payload, Mapping) or payload.get("category") != "spot":
        raise BybitRestProtocolError("tickers result must be a spot object")
    items = payload.get("list")
    if not isinstance(items, list) or len(items) > MAX_TICKERS:
        raise BybitRestProtocolError("tickers list must be bounded")
    if received_time_ms <= 0:
        raise ValueError("received_time_ms must be positive")
    tickers: list[BookTicker] = []
    activities: list[MarketActivity] = []
    rejections: list[BybitTickerRejection] = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            raise BybitRestProtocolError(f"ticker at index {index} must be an object")
        label = str(raw.get("symbol", f"index {index}"))[:64]
        try:
            symbol = _symbol(raw)
            if symbol in seen:
                raise BybitRestProtocolError(f"duplicate ticker symbol: {symbol}")
            seen.add(symbol)
            tickers.append(
                BookTicker(
                    symbol,
                    _decimal(raw, "bid1Price"),
                    _decimal(raw, "bid1Size"),
                    _decimal(raw, "ask1Price"),
                    _decimal(raw, "ask1Size"),
                    received_time_ms,
                )
            )
            activities.append(
                MarketActivity(
                    symbol,
                    _decimal(raw, "turnover24h", allow_zero=True),
                    received_time_ms,
                )
            )
        except ValueError as error:
            rejections.append(BybitTickerRejection(label, str(error)))
    return NormalizedBybitTickers(
        tuple(sorted(tickers, key=lambda item: item.symbol)),
        tuple(sorted(activities, key=lambda item: item.symbol)),
        tuple(sorted(rejections, key=lambda item: item.symbol)),
    )


def normalize_price_limit(payload: Any, *, received_time_ms: int) -> PriceLimit:
    if not isinstance(payload, Mapping):
        raise BybitRestProtocolError("price-limit result must be an object")
    try:
        symbol = _symbol(payload)
        source_raw = payload.get("ts")
        if not isinstance(source_raw, str) or not source_raw.isascii() or not source_raw.isdigit():
            raise ValueError("invalid ts")
        source_time_ms = int(source_raw)
        return PriceLimit(
            symbol=symbol,
            enabled=True,
            max_buy_price=_decimal(payload, "buyLmt"),
            min_sell_price=_decimal(payload, "sellLmt"),
            source_time_ms=source_time_ms,
            received_time_ms=received_time_ms,
        )
    except ValueError as error:
        raise BybitRestProtocolError(str(error)) from error


Sleep = Callable[[float], Awaitable[None]]
NowMs = Callable[[], int]


class BybitRestClient:
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

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, path: str, params: Mapping[str, str]) -> Any:
        for attempt in range(len(self._retry_delays) + 1):
            try:
                response = await self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == len(self._retry_delays):
                    raise BybitRestError(f"GET {path} failed after retries") from error
                await self._sleep(self._retry_delays[attempt])
                continue
            if response.status_code == 429:
                if attempt == len(self._retry_delays):
                    raise BybitRestError(f"GET {path} remained rate limited")
                await self._sleep(self._retry_delays[attempt])
                continue
            if response.status_code >= 500:
                if attempt == len(self._retry_delays):
                    raise BybitRestError(f"GET {path} failed with {response.status_code}")
                await self._sleep(self._retry_delays[attempt])
                continue
            if response.status_code >= 400:
                raise BybitRestError(f"GET {path} failed with {response.status_code}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise BybitRestProtocolError("response exceeds byte limit")
            try:
                payload = response.json()
            except ValueError as error:
                raise BybitRestProtocolError("response is not JSON") from error
            if not isinstance(payload, Mapping):
                raise BybitRestProtocolError("V5 response must be an object")
            if payload.get("retCode") != 0:
                raise BybitRestProtocolError(
                    f"V5 response rejected: {payload.get('retCode')} {payload.get('retMsg')}"
                )
            return payload.get("result")
        raise AssertionError("unreachable")

    async def instruments(self) -> NormalizedBybitInstruments:
        payload = await self._request(
            "/v5/market/instruments-info", {"category": "spot"}
        )
        return normalize_instruments(payload, taker_commission=self._fee)

    async def tickers(self) -> NormalizedBybitTickers:
        payload = await self._request("/v5/market/tickers", {"category": "spot"})
        return normalize_tickers(payload, received_time_ms=self._now_ms())

    async def calibrate_clock(self) -> ServerClock:
        started = self._now_ms()
        payload = await self._request("/v5/market/time", {})
        finished = self._now_ms()
        if not isinstance(payload, Mapping):
            raise BybitRestProtocolError("time result must be an object")
        raw = payload.get("timeSecond")
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit():
            raise BybitRestProtocolError("invalid timeSecond")
        server_time = int(raw) * 1000
        return ServerClock(server_time - (started + finished) // 2, finished - started, finished)

    async def price_limit(self, symbol: str) -> PriceLimit:
        self._validate_symbol(symbol)
        payload = await self._request(
            "/v5/market/price-limit", {"category": "spot", "symbol": symbol}
        )
        return normalize_price_limit(payload, received_time_ms=self._now_ms())

    async def depth_snapshot(self, symbol: str, *, limit: int = 1_000) -> BybitDepthSnapshot:
        self._validate_symbol(symbol)
        if limit not in {1, 50, 200, 1_000}:
            raise ValueError("Bybit depth limit must be 1, 50, 200, or 1000")
        payload = await self._request(
            "/v5/market/orderbook",
            {"category": "spot", "symbol": symbol, "limit": str(limit)},
        )
        return normalize_depth_snapshot(payload, received_time_ms=self._now_ms())

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if not symbol or symbol != symbol.strip().upper() or len(symbol) > 64:
            raise ValueError("invalid Bybit symbol")
