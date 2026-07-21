from decimal import Decimal

import httpx
import pytest

from tri_arb.exchange.bybit import BybitRestClient, normalize_price_limit, normalize_tickers


def test_normalizes_tickers_and_explicit_price_limit() -> None:
    tickers = normalize_tickers(
        {
            "category": "spot",
            "list": [{
                "symbol": "BTCUSDT",
                "bid1Price": "100",
                "bid1Size": "2",
                "ask1Price": "101",
                "ask1Size": "3",
                "turnover24h": "1234.5",
            }],
        },
        received_time_ms=1000,
    )
    limit = normalize_price_limit(
        {"symbol": "BTCUSDT", "buyLmt": "110", "sellLmt": "90", "ts": "990"},
        received_time_ms=1000,
    )

    assert tickers.tickers[0].ask_price == Decimal("101")
    assert tickers.activities[0].quote_volume == Decimal("1234.5")
    assert limit.max_buy_price == Decimal("110")
    assert limit.min_sell_price == Decimal("90")
    assert limit.source_time_ms == 990


@pytest.mark.asyncio
async def test_calls_only_public_v5_market_endpoints() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, dict(request.url.params)))
        results = {
            "/v5/market/instruments-info": {"category": "spot", "list": []},
            "/v5/market/tickers": {"category": "spot", "list": []},
            "/v5/market/time": {"timeSecond": "1"},
            "/v5/market/price-limit": {
                "symbol": "BTCUSDT", "buyLmt": "110", "sellLmt": "90", "ts": "990"
            },
            "/v5/market/orderbook": {
                "s": "BTCUSDT", "b": [["100", "1"]], "a": [["101", "1"]],
                "ts": 1000, "u": 10, "seq": 20, "cts": 990,
            },
        }
        return httpx.Response(
            200,
            json={"retCode": 0, "retMsg": "OK", "result": results[request.url.path]},
        )

    moments = iter([900, 910, 920, 930, 940])
    async with httpx.AsyncClient(
        base_url="https://api.bybit.com", transport=httpx.MockTransport(handler)
    ) as client:
        adapter = BybitRestClient(
            "https://api.bybit.com",
            taker_commission=Decimal("0.002"),
            client=client,
            now_ms=lambda: next(moments),
        )
        await adapter.instruments()
        await adapter.tickers()
        clock = await adapter.calibrate_clock()
        await adapter.price_limit("BTCUSDT")
        snapshot = await adapter.depth_snapshot("BTCUSDT", limit=50)

    assert clock.offset_ms == 85
    assert snapshot.update_id == 10
    assert calls == [
        ("/v5/market/instruments-info", {"category": "spot"}),
        ("/v5/market/tickers", {"category": "spot"}),
        ("/v5/market/time", {}),
        ("/v5/market/price-limit", {"category": "spot", "symbol": "BTCUSDT"}),
        ("/v5/market/orderbook", {"category": "spot", "symbol": "BTCUSDT", "limit": "50"}),
    ]
