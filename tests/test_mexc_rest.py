import asyncio
from decimal import Decimal

import httpx
import pytest

from tri_arb.exchange.mexc import (
    MexcRestClient,
    MexcRestError,
    MexcRestProtocolError,
    normalize_book_tickers,
)


def _ticker(**overrides):
    payload = {
        "symbol": "BTCUSDT",
        "bidPrice": "100.10",
        "bidQty": "2.5",
        "askPrice": "100.20",
        "askQty": "3.5",
    }
    payload.update(overrides)
    return payload


def _client(handler, **overrides):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.mexc.test", transport=transport)
    return MexcRestClient(
        "https://unused.test",
        client=http_client,
        retry_delays=(),
        **overrides,
    ), http_client


def test_normalizes_book_tickers_as_decimals_and_quarantines_invalid_markets() -> None:
    result = normalize_book_tickers(
        [_ticker(), _ticker(symbol="ETHUSDT", bidPrice="NaN")],
        received_time_ms=1_700_000_000_000,
    )
    (ticker,) = result.tickers

    assert ticker.symbol == "BTCUSDT"
    assert ticker.bid_price == Decimal("100.10")
    assert ticker.ask_quantity == Decimal("3.5")
    assert result.rejections[0].symbol == "ETHUSDT"
    assert "bidPrice" in result.rejections[0].reason


@pytest.mark.parametrize(
    "payload",
    [
        {},
        ["not-an-object"],
        [_ticker(), _ticker()],
    ],
)
def test_rejects_structurally_unsafe_book_ticker_responses(payload) -> None:
    with pytest.raises(MexcRestProtocolError):
        normalize_book_tickers(payload, received_time_ms=1)


def test_bounds_book_ticker_identity_and_decimal_text() -> None:
    result = normalize_book_tickers(
        [
            _ticker(symbol="S" * 65),
            _ticker(symbol="ETHUSDT", bidPrice="1" * 129),
            _ticker(symbol="SOLUSDT", bidPrice="1e999"),
        ],
        received_time_ms=1,
    )
    assert result.tickers == ()
    assert len(result.rejections) == 3
    assert all(len(rejection.symbol) <= 64 for rejection in result.rejections)


@pytest.mark.asyncio
async def test_calls_public_endpoints_and_calibrates_clock_at_request_midpoint() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        payloads = {
            "/api/v3/ping": {},
            "/api/v3/time": {"serverTime": 1_100},
            "/api/v3/exchangeInfo": {"symbols": []},
            "/api/v3/ticker/bookTicker": [_ticker()],
        }
        return httpx.Response(200, json=payloads[request.url.path])

    times = iter([1_000, 1_020, 1_030])
    client, http_client = _client(handler, now_ms=lambda: next(times))
    async with http_client:
        await client.ping()
        clock = await client.calibrate_clock()
        exchange_info = await client.exchange_info()
        book_tickers = await client.book_tickers()

    assert clock.offset_ms == 90
    assert clock.round_trip_ms == 20
    assert exchange_info.markets == ()
    assert book_tickers.tickers[0].received_time_ms == 1_030
    assert paths == [
        "/api/v3/ping",
        "/api/v3/time",
        "/api/v3/exchangeInfo",
        "/api/v3/ticker/bookTicker",
    ]


@pytest.mark.asyncio
async def test_retries_server_errors_with_exponential_jittered_backoff() -> None:
    calls = 0
    sleeps = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://api.mexc.test", transport=transport
    ) as http_client:
        client = MexcRestClient(
            "https://unused.test",
            client=http_client,
            retry_delays=(1, 2),
            jitter=lambda: 0.5,
            sleep=lambda delay: _record_sleep(sleeps, delay),
        )
        await client.ping()

    assert calls == 3
    assert sleeps == [1, 2]


async def _record_sleep(sleeps: list[float], delay: float) -> None:
    sleeps.append(delay)


@pytest.mark.asyncio
async def test_obeys_retry_after_without_jitter() -> None:
    calls = 0
    sleeps = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://api.mexc.test", transport=transport
    ) as http_client:
        client = MexcRestClient(
            "https://unused.test",
            client=http_client,
            retry_delays=(1,),
            sleep=lambda delay: _record_sleep(sleeps, delay),
        )
        await client.ping()

    assert sleeps == [7]


@pytest.mark.asyncio
async def test_fails_fast_for_non_retryable_client_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(418, json={})

    client, http_client = _client(handler)
    async with http_client:
        with pytest.raises(MexcRestError, match="418"):
            await client.ping()

    assert calls == 1


@pytest.mark.asyncio
async def test_serializes_overlapping_full_book_ticker_requests() -> None:
    active = 0
    maximum_active = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return httpx.Response(200, json=[_ticker()])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://api.mexc.test", transport=transport
    ) as http_client:
        client = MexcRestClient(
            "https://unused.test",
            client=http_client,
            retry_delays=(),
            now_ms=lambda: 1,
        )
        await asyncio.gather(client.book_tickers(), client.book_tickers())

    assert maximum_active == 1
