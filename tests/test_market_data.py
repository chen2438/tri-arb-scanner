from decimal import Decimal

import pytest

from tri_arb.config import Settings
from tri_arb.domain.models import BookTicker, ConversionSide, MarketRules
from tri_arb.exchange.mexc import (
    NormalizedBookTickers,
    NormalizedExchangeInfo,
    ServerClock,
)
from tri_arb.market_data import MarketDataPhase, MarketDataService


def _market(symbol: str, base: str, quote: str) -> MarketRules:
    return MarketRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        base_asset_precision=8,
        min_base_quantity=Decimal("0.00000001"),
        min_quote_amount=Decimal("1"),
        max_quote_amount=Decimal("1000000"),
        taker_commission=Decimal("0.001"),
        allowed_sides=frozenset({ConversionSide.BUY, ConversionSide.SELL}),
    )


def _ticker(symbol: str, received_time_ms: int = 1_000) -> BookTicker:
    return BookTicker(
        symbol=symbol,
        bid_price=Decimal("1"),
        bid_quantity=Decimal("10"),
        ask_price=Decimal("1.01"),
        ask_quantity=Decimal("10"),
        received_time_ms=received_time_ms,
    )


class FakeRestClient:
    def __init__(self) -> None:
        self.markets = (
            _market("AUSDT", "A", "USDT"),
            _market("BA", "B", "A"),
            _market("BUSDT", "B", "USDT"),
        )

    async def exchange_info(self) -> NormalizedExchangeInfo:
        return NormalizedExchangeInfo(self.markets, ())

    async def calibrate_clock(self) -> ServerClock:
        return ServerClock(offset_ms=5, round_trip_ms=10, calibrated_at_ms=1_000)

    async def book_tickers(self) -> NormalizedBookTickers:
        return NormalizedBookTickers(
            (*(_ticker(market.symbol) for market in self.markets), _ticker("EXTRAUSDT")),
            (),
        )


@pytest.mark.asyncio
async def test_builds_coherent_ready_snapshot_from_public_rest_inputs() -> None:
    service = MarketDataService(
        Settings(mexc_ws_url="ws://127.0.0.1:1/ws", _env_file=None),
        rest_client=FakeRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1_000,
    )

    assert (await service.snapshot()).status.phase is MarketDataPhase.INITIALIZING
    await service.refresh_metadata()
    await service.calibrate_clock()
    await service.refresh_tickers()
    snapshot = await service.snapshot()

    assert snapshot.status.phase is MarketDataPhase.READY
    assert snapshot.status.market_count == 3
    assert snapshot.status.route_count == 2
    assert set(snapshot.tickers) == {"AUSDT", "BA", "BUSDT"}
    assert "EXTRAUSDT" not in snapshot.tickers


@pytest.mark.asyncio
async def test_reconciles_ranked_routes_into_complete_depth_subscription_plan() -> None:
    service = MarketDataService(
        Settings(mexc_ws_url="ws://127.0.0.1:1/ws", _env_file=None),
        rest_client=FakeRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 20_000,
    )
    await service.refresh_metadata()
    routes = (await service.snapshot()).routes

    await service.set_ranked_routes((routes[0],))
    plan = await service.reconcile_depth()
    snapshot = await service.snapshot()

    assert plan.selected_route_ids == (routes[0].route_id,)
    assert len(plan.symbols) == 3
    assert snapshot.status.subscription_count == 3
    assert snapshot.depth_books == {}
