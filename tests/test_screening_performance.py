from __future__ import annotations

from decimal import Decimal
from time import perf_counter

from tri_arb.domain.models import (
    BookTicker,
    ConversionEdge,
    ConversionSide,
    MarketRules,
    TriangularRoute,
)
from tri_arb.scanner import screen_routes

MARKET_COUNT = 3_000
ROUTE_COUNT = 2_000
MAX_SCREEN_SECONDS = 0.250


def _market(symbol: str, base: str, quote: str) -> MarketRules:
    return MarketRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        base_asset_precision=8,
        min_base_quantity=Decimal("0.00000001"),
        min_quote_amount=Decimal("0.01"),
        max_quote_amount=Decimal("1000000"),
        taker_commission=Decimal("0.001"),
        allowed_sides=frozenset({ConversionSide.BUY, ConversionSide.SELL}),
    )


def _edge(
    market: MarketRules,
    from_asset: str,
    to_asset: str,
    side: ConversionSide,
) -> ConversionEdge:
    return ConversionEdge(market, from_asset, to_asset, side)


def _fixture() -> tuple[tuple[TriangularRoute, ...], dict[str, BookTicker]]:
    assets = tuple(f"A{index:04d}" for index in range(1_000))
    spokes = {asset: _market(f"{asset}USDT", asset, "USDT") for asset in assets}
    routes: list[TriangularRoute] = []
    markets = {market.symbol: market for market in spokes.values()}
    for index in range(ROUTE_COUNT):
        asset_a = assets[index % len(assets)]
        asset_b = assets[(index * 17 + 1) % len(assets)]
        cross = _market(f"X{index:04d}", asset_b, asset_a)
        markets[cross.symbol] = cross
        edges = (
            _edge(spokes[asset_a], "USDT", asset_a, ConversionSide.BUY),
            _edge(cross, asset_a, asset_b, ConversionSide.BUY),
            _edge(spokes[asset_b], asset_b, "USDT", ConversionSide.SELL),
        )
        routes.append(
            TriangularRoute(
                route_id=f"synthetic-{index:04d}",
                assets=("USDT", asset_a, asset_b, "USDT"),
                edges=edges,
            )
        )
    tickers = {
        symbol: BookTicker(
            symbol=symbol,
            bid_price=Decimal("1.001"),
            bid_quantity=Decimal("1000000"),
            ask_price=Decimal("1.002"),
            ask_quantity=Decimal("1000000"),
            received_time_ms=1,
        )
        for symbol in markets
    }
    assert len(markets) == MARKET_COUNT
    assert len(routes) == ROUTE_COUNT
    return tuple(routes), tickers


def test_full_broad_screen_meets_ci_capacity_budget() -> None:
    routes, tickers = _fixture()

    started = perf_counter()
    candidates = screen_routes(routes, tickers, Decimal("100"), limit=20)
    elapsed = perf_counter() - started

    assert len(candidates) == 20
    assert elapsed < MAX_SCREEN_SECONDS, f"broad screen took {elapsed * 1_000:.1f} ms"
