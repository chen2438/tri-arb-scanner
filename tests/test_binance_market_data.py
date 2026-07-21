from decimal import Decimal

import pytest

from tri_arb.config import Settings
from tri_arb.exchange.binance import normalize_exchange_info, normalize_tickers
from tri_arb.exchange.binance.market_data import BinanceMarketDataService
from tri_arb.exchange.mexc import ServerClock


def _symbol(symbol: str, base: str, quote: str):
    return {
        "symbol": symbol,
        "status": "TRADING",
        "baseAsset": base,
        "quoteAsset": quote,
        "isSpotTradingAllowed": True,
        "orderTypes": ["LIMIT", "MARKET"],
        "filters": [
            {
                "filterType": "LOT_SIZE",
                "minQty": "0.0001",
                "maxQty": "1000000",
                "stepSize": "0.0001",
            },
            {"filterType": "NOTIONAL", "minNotional": "1", "maxNotional": "1000000"},
        ],
    }


def _execution_rule(symbol: str):
    return {
        "symbol": symbol,
        "rules": [
            {
                "ruleType": "PRICE_RANGE",
                "bidLimitMultUp": "1.15",
                "bidLimitMultDown": "0.85",
                "askLimitMultUp": "1.15",
                "askLimitMultDown": "0.85",
            }
        ],
    }


def _book(symbol: str, bid: str, ask: str):
    return {
        "symbol": symbol,
        "bidPrice": bid,
        "bidQty": "1000",
        "askPrice": ask,
        "askQty": "1000",
    }


class FakeBinanceRestClient:
    async def exchange_info(self):
        symbols = [
            _symbol("BTCUSDT", "BTC", "USDT"),
            _symbol("ETHBTC", "ETH", "BTC"),
            _symbol("ETHUSDT", "ETH", "USDT"),
        ]
        return normalize_exchange_info(
            {"symbols": symbols},
            {"symbolRules": [_execution_rule(item["symbol"]) for item in symbols]},
            taker_commission=Decimal("0.001"),
        )

    async def tickers(self):
        books = [
            _book("BTCUSDT", "100", "101"),
            _book("ETHBTC", "0.05", "0.051"),
            _book("ETHUSDT", "5", "5.1"),
        ]
        activities = [
            {"symbol": item["symbol"], "quoteVolume": "1000000"} for item in books
        ]
        return normalize_tickers(books, activities, received_time_ms=1000)

    async def calibrate_clock(self):
        return ServerClock(0, 1, 1000)

    async def depth_snapshot(self, _symbol: str):
        raise AssertionError("depth snapshot should not be called in this test")


@pytest.mark.asyncio
async def test_builds_independent_binance_routes_and_core_depth_plan() -> None:
    service = BinanceMarketDataService(
        Settings(_env_file=None),
        rest_client=FakeBinanceRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1000,
    )
    await service.refresh_metadata()
    await service.refresh_tickers()
    await service.calibrate_clock()
    snapshot = await service.snapshot()

    assert snapshot.status.ready
    assert len(snapshot.routes) == 2
    assert all(route.exchange == "BINANCE" for route in snapshot.routes)
    assert all(route.route_id.startswith("BINANCE|") for route in snapshot.routes)
    assert snapshot.status.core_market_count == 3
    assert snapshot.status.core_route_count == 2

    await service.set_ranked_routes(snapshot.routes)
    plan = await service.reconcile_depth()
    assert set(plan.selected_route_ids) == {route.route_id for route in snapshot.routes}
    assert plan.symbols == {"BTCUSDT", "ETHBTC", "ETHUSDT"}
