from decimal import Decimal

import pytest

from tri_arb.config import Settings
from tri_arb.domain.models import PriceLimit
from tri_arb.exchange.bybit import normalize_instruments, normalize_tickers
from tri_arb.exchange.bybit.market_data import BybitMarketDataService
from tri_arb.exchange.mexc import ServerClock


def _instrument(symbol: str, base: str, quote: str):
    return {
        "symbol": symbol,
        "baseCoin": base,
        "quoteCoin": quote,
        "status": "Trading",
        "lotSizeFilter": {
            "basePrecision": "0.0001",
            "minOrderAmt": "1",
            "maxMarketOrderQty": "1000000",
        },
    }


def _ticker(symbol: str, bid: str, ask: str):
    return {
        "symbol": symbol,
        "bid1Price": bid,
        "bid1Size": "1000",
        "ask1Price": ask,
        "ask1Size": "1000",
        "turnover24h": "1000000",
    }


class FakeBybitRestClient:
    async def instruments(self):
        return normalize_instruments(
            {
                "category": "spot",
                "list": [
                    _instrument("BTCUSDT", "BTC", "USDT"),
                    _instrument("ETHBTC", "ETH", "BTC"),
                    _instrument("ETHUSDT", "ETH", "USDT"),
                ],
            },
            taker_commission=Decimal("0.002"),
        )

    async def tickers(self):
        return normalize_tickers(
            {
                "category": "spot",
                "list": [
                    _ticker("BTCUSDT", "100", "101"),
                    _ticker("ETHBTC", "0.05", "0.051"),
                    _ticker("ETHUSDT", "5", "5.1"),
                ],
            },
            received_time_ms=1000,
        )

    async def calibrate_clock(self):
        return ServerClock(0, 1, 1000)

    async def price_limit(self, symbol: str):
        return PriceLimit(
            symbol, True, Decimal("1000000"), Decimal("0.000001"), 1000, 1000
        )


@pytest.mark.asyncio
async def test_builds_independent_bybit_routes_depth_plan_and_price_limits() -> None:
    service = BybitMarketDataService(
        Settings(_env_file=None),
        rest_client=FakeBybitRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1000,
    )
    await service.refresh_metadata()
    await service.refresh_tickers()
    await service.calibrate_clock()
    snapshot = await service.snapshot()

    assert snapshot.status.ready
    assert len(snapshot.routes) == 2
    assert all(route.exchange == "BYBIT" for route in snapshot.routes)
    assert all(route.route_id.startswith("BYBIT|") for route in snapshot.routes)
    assert snapshot.status.core_market_count == 3
    assert snapshot.status.core_route_count == 2

    await service.set_ranked_routes(snapshot.routes)
    plan = await service.reconcile_depth()
    limits = await service.refresh_price_limits()
    refreshed = await service.snapshot()

    assert set(plan.selected_route_ids) == {route.route_id for route in snapshot.routes}
    assert plan.symbols == {"BTCUSDT", "ETHBTC", "ETHUSDT"}
    assert {value.symbol for value in limits} == plan.symbols
    assert refreshed.status.price_reference_count == 3
    assert set(refreshed.price_limits) == plan.symbols
