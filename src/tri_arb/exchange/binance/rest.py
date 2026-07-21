"""Bounded public-only Binance Spot REST client."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from tri_arb.domain.models import BookTicker, MarketActivity
from tri_arb.exchange.binance.metadata import (
    NormalizedBinanceExchangeInfo,
    normalize_exchange_info,
)
from tri_arb.exchange.mexc.rest import ServerClock

MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_TICKERS = 10_000
MAX_DECIMAL_LENGTH = 128
DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)


class BinanceRestError(RuntimeError):
    pass


class BinanceRestProtocolError(BinanceRestError):
    pass


@dataclass(frozen=True, slots=True)
class BinanceTickerRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedBinanceTickers:
    tickers: tuple[BookTicker, ...]
    activities: tuple[MarketActivity, ...]
    rejections: tuple[BinanceTickerRejection, ...]


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
    if not isinstance(value, str) or not value or value != value.strip().upper() or len(value) > 64:
        raise ValueError("invalid symbol")
    return value


def normalize_tickers(
    books_payload: Any,
    activity_payload: Any,
    *,
    received_time_ms: int,
) -> NormalizedBinanceTickers:
    if not isinstance(books_payload, list) or len(books_payload) > MAX_TICKERS:
        raise BinanceRestProtocolError("bookTicker response must be a bounded list")
    if not isinstance(activity_payload, list) or len(activity_payload) > MAX_TICKERS:
        raise BinanceRestProtocolError("24hr response must be a bounded list")
    if received_time_ms <= 0:
        raise ValueError("received_time_ms must be positive")
    activity_by_symbol: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(activity_payload):
        if not isinstance(raw, Mapping):
            raise BinanceRestProtocolError(f"24hr item at index {index} must be an object")
        try:
            symbol = _symbol(raw)
        except ValueError as error:
            raise BinanceRestProtocolError(str(error)) from error
        if symbol in activity_by_symbol:
            raise BinanceRestProtocolError(f"duplicate 24hr symbol: {symbol}")
        activity_by_symbol[symbol] = raw

    tickers: list[BookTicker] = []
    activities: list[MarketActivity] = []
    rejections: list[BinanceTickerRejection] = []
    seen: set[str] = set()
    for index, raw in enumerate(books_payload):
        if not isinstance(raw, Mapping):
            raise BinanceRestProtocolError(f"bookTicker item at index {index} must be an object")
        label = str(raw.get("symbol", f"index {index}"))[:64]
        try:
            symbol = _symbol(raw)
            if symbol in seen:
                raise BinanceRestProtocolError(f"duplicate bookTicker symbol: {symbol}")
            seen.add(symbol)
            activity = activity_by_symbol.get(symbol)
            if activity is None:
                raise ValueError("missing 24hr activity")
            ticker = BookTicker(
                symbol,
                _decimal(raw, "bidPrice"),
                _decimal(raw, "bidQty"),
                _decimal(raw, "askPrice"),
                _decimal(raw, "askQty"),
                received_time_ms,
            )
            market_activity = MarketActivity(
                symbol,
                _decimal(activity, "quoteVolume", allow_zero=True),
                received_time_ms,
            )
        except ValueError as error:
            rejections.append(BinanceTickerRejection(label, str(error)))
            continue
        tickers.append(ticker)
        activities.append(market_activity)
    return NormalizedBinanceTickers(
        tuple(sorted(tickers, key=lambda item: item.symbol)),
        tuple(sorted(activities, key=lambda item: item.symbol)),
        tuple(sorted(rejections, key=lambda item: item.symbol)),
    )


Sleep = Callable[[float], Awaitable[None]]
NowMs = Callable[[], int]


class BinanceRestClient:
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
        self._ticker_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        for attempt in range(len(self._retry_delays) + 1):
            try:
                response = await self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == len(self._retry_delays):
                    raise BinanceRestError(f"GET {path} failed after retries") from error
                await self._sleep(self._retry_delays[attempt])
                continue
            if response.status_code in {418, 429}:
                if attempt == len(self._retry_delays):
                    raise BinanceRestError(f"GET {path} remained rate limited")
                raw_retry = response.headers.get("Retry-After", "1")
                try:
                    retry_after = float(raw_retry)
                except ValueError as error:
                    raise BinanceRestProtocolError("invalid Retry-After header") from error
                if not 0 <= retry_after <= 3600:
                    raise BinanceRestProtocolError("unsafe Retry-After duration")
                await self._sleep(retry_after)
                continue
            if response.status_code >= 500:
                if attempt == len(self._retry_delays):
                    raise BinanceRestError(f"GET {path} failed with {response.status_code}")
                await self._sleep(self._retry_delays[attempt])
                continue
            if response.status_code >= 400:
                raise BinanceRestError(f"GET {path} failed with {response.status_code}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise BinanceRestProtocolError("response exceeds byte limit")
            try:
                return response.json()
            except ValueError as error:
                raise BinanceRestProtocolError("response is not JSON") from error
        raise AssertionError("unreachable")

    async def exchange_info(self) -> NormalizedBinanceExchangeInfo:
        exchange_info, execution_rules = await asyncio.gather(
            self._request("/api/v3/exchangeInfo", {"showPermissionSets": "false"}),
            self._request("/api/v3/executionRules", {"symbolStatus": "TRADING"}),
        )
        return normalize_exchange_info(
            exchange_info, execution_rules, taker_commission=self._fee
        )

    async def tickers(self) -> NormalizedBinanceTickers:
        async with self._ticker_lock:
            books, activities = await asyncio.gather(
                self._request("/api/v3/ticker/bookTicker", {"symbolStatus": "TRADING"}),
                self._request("/api/v3/ticker/24hr", {"type": "MINI"}),
            )
            return normalize_tickers(
                books, activities, received_time_ms=self._now_ms()
            )

    async def calibrate_clock(self) -> ServerClock:
        started = self._now_ms()
        payload = await self._request("/api/v3/time")
        finished = self._now_ms()
        if not isinstance(payload, Mapping):
            raise BinanceRestProtocolError("time response must be an object")
        server_time = payload.get("serverTime")
        if isinstance(server_time, bool) or not isinstance(server_time, int) or server_time <= 0:
            raise BinanceRestProtocolError("invalid serverTime")
        return ServerClock(server_time - (started + finished) // 2, finished - started, finished)
