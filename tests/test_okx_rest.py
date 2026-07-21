from decimal import Decimal

import httpx
import pytest

from tri_arb.exchange.okx import OkxRestClient, OkxRestProtocolError, normalize_tickers


def _ticker(**overrides):
    value = {
        "instType": "SPOT",
        "instId": "BTC-USDT",
        "bidPx": "100.1",
        "bidSz": "2",
        "askPx": "100.2",
        "askSz": "3",
        "volCcy24h": "123456.7",
    }
    value.update(overrides)
    return value


def test_normalizes_top_of_book_and_quote_volume_together() -> None:
    result = normalize_tickers([_ticker()], received_time_ms=1234)

    assert result.tickers[0].bid_price == Decimal("100.1")
    assert result.activities[0].quote_volume == Decimal("123456.7")
    assert result.rejections == ()


def test_quarantines_crossed_or_incomplete_ticker() -> None:
    result = normalize_tickers(
        [_ticker(), _ticker(instId="ETH-USDT", bidPx="101", askPx="100")],
        received_time_ms=1,
    )

    assert [item.symbol for item in result.tickers] == ["BTC-USDT"]
    assert result.rejections[0].symbol == "ETH-USDT"


@pytest.mark.asyncio
async def test_calls_only_public_okx_endpoints_and_calibrates_clock() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.url.path, request.url.params.get("instType")))
        data = {
            "/api/v5/public/instruments": [],
            "/api/v5/market/tickers": [_ticker()],
            "/api/v5/public/time": [{"ts": "1100"}],
        }[request.url.path]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": data})

    times = iter([1000, 1010, 1020, 1040])
    async with httpx.AsyncClient(
        base_url="https://www.okx.test", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = OkxRestClient(
            "https://unused.test",
            taker_commission=Decimal("0.001"),
            client=http_client,
            retry_delays=(),
            now_ms=lambda: next(times),
        )
        await client.instruments()
        tickers = await client.tickers()
        clock = await client.calibrate_clock()

    assert tickers.tickers[0].received_time_ms == 1000
    assert clock.offset_ms == 85
    assert clock.round_trip_ms == 10
    assert paths == [
        ("/api/v5/public/instruments", "SPOT"),
        ("/api/v5/market/tickers", "SPOT"),
        ("/api/v5/public/time", None),
    ]


@pytest.mark.asyncio
async def test_rejects_unsuccessful_okx_envelope() -> None:
    async with httpx.AsyncClient(
        base_url="https://www.okx.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"code": "50001", "data": []})
        ),
    ) as http_client:
        client = OkxRestClient(
            "https://unused.test",
            taker_commission=Decimal("0.001"),
            client=http_client,
            retry_delays=(),
        )
        with pytest.raises(OkxRestProtocolError, match="unsuccessful"):
            await client.instruments()
