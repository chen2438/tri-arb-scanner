from decimal import Decimal

import pytest

from tri_arb.config import Settings
from tri_arb.domain.models import PriceLimit
from tri_arb.exchange.mexc import ServerClock
from tri_arb.exchange.okx import normalize_instruments, normalize_tickers
from tri_arb.exchange.okx.market_data import OkxMarketDataService


def _instrument(symbol: str, base: str, quote: str):
    return {
        "instType": "SPOT",
        "instId": symbol,
        "baseCcy": base,
        "quoteCcy": quote,
        "state": "live",
        "lotSz": "0.0001",
        "minSz": "0.001",
    }


def _ticker(symbol: str, bid: str, ask: str):
    return {
        "instType": "SPOT",
        "instId": symbol,
        "bidPx": bid,
        "bidSz": "1000",
        "askPx": ask,
        "askSz": "1000",
        "volCcy24h": "1000000",
    }


class FakeOkxRestClient:
    async def instruments(self):
        return normalize_instruments(
            [
                _instrument("BTC-USDT", "BTC", "USDT"),
                _instrument("ETH-BTC", "ETH", "BTC"),
                _instrument("ETH-USDT", "ETH", "USDT"),
            ],
            taker_commission=Decimal("0.001"),
        )

    async def tickers(self):
        return normalize_tickers(
            [
                _ticker("BTC-USDT", "100", "101"),
                _ticker("ETH-BTC", "0.05", "0.051"),
                _ticker("ETH-USDT", "5", "5.1"),
            ],
            received_time_ms=1000,
        )

    async def calibrate_clock(self):
        return ServerClock(0, 1, 1000)

    async def price_limit(self, symbol: str):
        return PriceLimit(symbol, True, Decimal("1000000"), Decimal("0.000001"), 1000, 1000)


@pytest.mark.asyncio
async def test_builds_independent_okx_routes_and_core_depth_plan() -> None:
    service = OkxMarketDataService(
        Settings(),
        rest_client=FakeOkxRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1000,
    )
    await service.refresh_metadata()
    await service.refresh_tickers()
    await service.calibrate_clock()
    snapshot = await service.snapshot()

    assert snapshot.status.ready
    assert len(snapshot.routes) == 2
    assert all(route.exchange == "OKX" for route in snapshot.routes)
    assert all(route.route_id.startswith("OKX|") for route in snapshot.routes)
    assert snapshot.status.core_market_count == 3
    assert snapshot.status.core_route_count == 2


@pytest.mark.asyncio
async def test_ranked_okx_routes_are_selected_without_mexc_symbols() -> None:
    service = OkxMarketDataService(
        Settings(),
        rest_client=FakeOkxRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 2000,
    )
    await service.refresh_metadata()
    await service.refresh_tickers()
    snapshot = await service.snapshot()
    await service.set_ranked_routes(snapshot.routes)
    plan = await service.reconcile_depth()
    limits = await service.refresh_price_limits()

    assert set(plan.selected_route_ids) == {route.route_id for route in snapshot.routes}
    assert plan.symbols == {"BTC-USDT", "ETH-BTC", "ETH-USDT"}
    assert {value.symbol for value in limits} == plan.symbols
