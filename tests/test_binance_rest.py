from decimal import Decimal

import httpx
import pytest

from tri_arb.exchange.binance import BinanceRestClient, normalize_tickers


def test_normalizes_books_and_activity_with_decimal_values() -> None:
    result = normalize_tickers(
        [{"symbol": "BTCUSDT", "bidPrice": "100", "bidQty": "2", "askPrice": "101", "askQty": "3"}],
        [{"symbol": "BTCUSDT", "quoteVolume": "1234.5"}],
        received_time_ms=1000,
    )
    assert result.tickers[0].ask_price == Decimal("101")
    assert result.activities[0].quote_volume == Decimal("1234.5")


@pytest.mark.asyncio
async def test_calls_only_public_endpoints_and_calibrates_clock() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, dict(request.url.params)))
        payload = {
            "/api/v3/exchangeInfo": {"symbols": []},
            "/api/v3/executionRules": {"symbolRules": []},
            "/api/v3/ticker/bookTicker": [],
            "/api/v3/ticker/24hr": [],
            "/api/v3/time": {"serverTime": 1100},
            "/api/v3/depth": {
                "lastUpdateId": 10,
                "bids": [["100", "1"]],
                "asks": [["101", "1"]],
            },
        }[request.url.path]
        return httpx.Response(200, json=payload)

    moments = iter([1000, 1010, 1020, 1030])
    async with httpx.AsyncClient(
        base_url="https://api.binance.com", transport=httpx.MockTransport(handler)
    ) as client:
        adapter = BinanceRestClient(
            "https://api.binance.com",
            taker_commission=Decimal("0.001"),
            client=client,
            now_ms=lambda: next(moments),
        )
        await adapter.exchange_info()
        await adapter.tickers()
        clock = await adapter.calibrate_clock()
        snapshot = await adapter.depth_snapshot("BTCUSDT")

    assert clock.offset_ms == 85
    assert snapshot.last_update_id == 10
    assert calls == [
        ("/api/v3/exchangeInfo", {"showPermissionSets": "false"}),
        ("/api/v3/executionRules", {"symbolStatus": "TRADING"}),
        ("/api/v3/ticker/bookTicker", {"symbolStatus": "TRADING"}),
        ("/api/v3/ticker/24hr", {"type": "MINI"}),
        ("/api/v3/time", {}),
        ("/api/v3/depth", {"symbol": "BTCUSDT", "limit": "1000"}),
    ]
