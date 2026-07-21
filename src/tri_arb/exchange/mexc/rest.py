"""Strict, retrying client for MEXC public REST market data."""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from tri_arb.domain.models import BookTicker
from tri_arb.exchange.mexc.metadata import NormalizedExchangeInfo, normalize_exchange_info

MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_BOOK_TICKERS = 10_000
MAX_SYMBOL_LENGTH = 64
MAX_DECIMAL_LENGTH = 128
DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


class MexcRestError(RuntimeError):
    """Base error for public REST failures."""


class MexcRestProtocolError(MexcRestError):
    """Raised when MEXC returns a structurally unsafe response."""


@dataclass(frozen=True, slots=True)
class ServerClock:
    offset_ms: int
    round_trip_ms: int
    calibrated_at_ms: int

    def server_time_ms(self, local_time_ms: int) -> int:
        return local_time_ms + self.offset_ms


@dataclass(frozen=True, slots=True)
class BookTickerRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedBookTickers:
    tickers: tuple[BookTicker, ...]
    rejections: tuple[BookTickerRejection, ...]


Sleep = Callable[[float], Awaitable[None]]
NowMs = Callable[[], int]
Jitter = Callable[[], float]


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _positive_decimal(raw: Mapping[str, Any], field: str) -> Decimal:
    raw_value = raw.get(field)
    if (
        isinstance(raw_value, bool)
        or not isinstance(raw_value, (str, int))
        or len(str(raw_value)) > MAX_DECIMAL_LENGTH
    ):
        raise ValueError(f"invalid {field}")
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"invalid {field}") from error
    if not value.is_finite() or value <= 0 or abs(value.adjusted()) > 60:
        raise ValueError(f"invalid {field}")
    return value


def _symbol(raw: Mapping[str, Any]) -> str:
    value = raw.get("symbol")
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_SYMBOL_LENGTH
        or value != value.strip()
        or value != value.upper()
    ):
        raise ValueError("invalid symbol")
    return value


def normalize_book_tickers(payload: Any, *, received_time_ms: int) -> NormalizedBookTickers:
    if not isinstance(payload, list):
        raise MexcRestProtocolError("bookTicker response must be a list")
    if len(payload) > MAX_BOOK_TICKERS:
        raise MexcRestProtocolError("bookTicker response exceeds the market limit")
    if received_time_ms <= 0:
        raise ValueError("received_time_ms must be positive")

    tickers: list[BookTicker] = []
    rejections: list[BookTickerRejection] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, Mapping):
            raise MexcRestProtocolError(f"bookTicker at index {index} must be an object")
        raw_symbol = raw.get("symbol")
        if isinstance(raw_symbol, str) and len(raw_symbol) <= MAX_SYMBOL_LENGTH:
            if raw_symbol in seen:
                raise MexcRestProtocolError(f"duplicate bookTicker symbol: {raw_symbol}")
            seen.add(raw_symbol)
        try:
            ticker = BookTicker(
                symbol=_symbol(raw),
                bid_price=_positive_decimal(raw, "bidPrice"),
                bid_quantity=_positive_decimal(raw, "bidQty"),
                ask_price=_positive_decimal(raw, "askPrice"),
                ask_quantity=_positive_decimal(raw, "askQty"),
                received_time_ms=received_time_ms,
            )
        except ValueError as error:
            label = str(raw_symbol or f"index {index}")[:MAX_SYMBOL_LENGTH]
            rejections.append(BookTickerRejection(label, str(error)))
            continue
        tickers.append(ticker)
    return NormalizedBookTickers(
        tickers=tuple(sorted(tickers, key=lambda ticker: ticker.symbol)),
        rejections=tuple(sorted(rejections, key=lambda rejection: rejection.symbol)),
    )


class MexcRestClient:
    """Public-only MEXC REST adapter with bounded retries and serial ticker polling."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 3.0,
        retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
        sleep: Sleep = asyncio.sleep,
        now_ms: NowMs = _now_ms,
        jitter: Jitter = random.random,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._owns_client = client is None
        self._retry_delays = tuple(retry_delays)
        if any(delay < 0 or delay > 30 for delay in self._retry_delays):
            raise ValueError("retry delays must be between 0 and 30 seconds")
        self._sleep = sleep
        self._now_ms = now_ms
        self._jitter = jitter
        self._book_ticker_lock = asyncio.Lock()

    async def __aenter__(self) -> MexcRestClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _retry_after(self, response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return self._retry_delays[0] if self._retry_delays else 1.0
        try:
            seconds = float(raw)
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(raw)
            except (TypeError, ValueError) as error:
                raise MexcRestProtocolError("invalid Retry-After header") from error
            if parsed is None:
                raise MexcRestProtocolError("invalid Retry-After header") from None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            now = datetime.fromtimestamp(self._now_ms() / 1000, tz=UTC)
            seconds = max(0.0, (parsed - now).total_seconds())
        if not 0 <= seconds <= 3600:
            raise MexcRestProtocolError("unsafe Retry-After duration")
        return seconds

    def _backoff(self, attempt: int) -> float:
        base = self._retry_delays[attempt]
        return min(30.0, base * (0.8 + 0.4 * self._jitter()))

    async def _request_json(self, path: str) -> Any:
        attempt = 0
        while True:
            try:
                response = await self._client.get(path)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt >= len(self._retry_delays):
                    raise MexcRestError(f"GET {path} failed after retries") from error
                await self._sleep(self._backoff(attempt))
                attempt += 1
                continue

            if response.status_code == 429:
                if attempt >= len(self._retry_delays):
                    raise MexcRestError(f"GET {path} remained rate limited")
                await self._sleep(self._retry_after(response))
                attempt += 1
                continue
            if response.status_code >= 500:
                if attempt >= len(self._retry_delays):
                    raise MexcRestError(f"GET {path} failed with {response.status_code}")
                await self._sleep(self._backoff(attempt))
                attempt += 1
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise MexcRestError(f"GET {path} failed with {response.status_code}") from error
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise MexcRestProtocolError(f"GET {path} response is too large")
            try:
                return response.json()
            except ValueError as error:
                raise MexcRestProtocolError(f"GET {path} returned invalid JSON") from error

    async def ping(self) -> None:
        payload = await self._request_json("/api/v3/ping")
        if payload != {}:
            raise MexcRestProtocolError("ping response must be an empty object")

    async def calibrate_clock(self) -> ServerClock:
        started_ms = self._now_ms()
        payload = await self._request_json("/api/v3/time")
        finished_ms = self._now_ms()
        if not isinstance(payload, Mapping):
            raise MexcRestProtocolError("time response must be an object")
        server_time = payload.get("serverTime")
        if isinstance(server_time, bool) or not isinstance(server_time, int) or server_time <= 0:
            raise MexcRestProtocolError("invalid serverTime")
        round_trip_ms = finished_ms - started_ms
        if round_trip_ms < 0:
            raise MexcRestProtocolError("local clock moved backwards during calibration")
        midpoint_ms = started_ms + round_trip_ms // 2
        return ServerClock(
            offset_ms=server_time - midpoint_ms,
            round_trip_ms=round_trip_ms,
            calibrated_at_ms=finished_ms,
        )

    async def exchange_info(self) -> NormalizedExchangeInfo:
        payload = await self._request_json("/api/v3/exchangeInfo")
        if not isinstance(payload, Mapping):
            raise MexcRestProtocolError("exchangeInfo response must be an object")
        return normalize_exchange_info(payload)

    async def book_tickers(self) -> NormalizedBookTickers:
        async with self._book_ticker_lock:
            payload = await self._request_json("/api/v3/ticker/bookTicker")
            return normalize_book_tickers(payload, received_time_ms=self._now_ms())
