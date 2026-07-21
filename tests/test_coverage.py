from decimal import Decimal

from tests.test_graph import _market
from tri_arb.domain import build_market_graph, enumerate_triangular_routes
from tri_arb.domain.coverage import select_core_coverage
from tri_arb.domain.models import BookTicker, MarketActivity


def _ticker(symbol: str, spread: str = "0.01") -> BookTicker:
    return BookTicker(
        symbol=symbol,
        bid_price=Decimal("1"),
        bid_quantity=Decimal("100"),
        ask_price=Decimal("1") + Decimal(spread),
        ask_quantity=Decimal("100"),
        received_time_ms=1,
    )


def test_selects_complete_routes_that_maximize_shared_path_coverage() -> None:
    markets = (
        _market("AUSDT", "A", "USDT"),
        _market("BUSDT", "B", "USDT"),
        _market("CUSDT", "C", "USDT"),
        _market("BA", "B", "A"),
        _market("CA", "C", "A"),
    )
    routes = enumerate_triangular_routes(build_market_graph(markets))
    tickers = {market.symbol: _ticker(market.symbol) for market in markets}
    activities = {
        market.symbol: MarketActivity(market.symbol, Decimal("1000"), 1) for market in markets
    }

    coverage = select_core_coverage(routes, tickers, activities, market_limit=5)

    assert coverage.symbols == ("AUSDT", "BA", "BUSDT", "CA", "CUSDT")
    assert len(coverage.covered_route_ids) == 4


def test_seeds_each_anchor_and_uses_volume_then_spread_as_quality_signals() -> None:
    markets = (
        _market("AUSDT", "A", "USDT"),
        _market("BA", "B", "A"),
        _market("BUSDT", "B", "USDT"),
        _market("CUSDC", "C", "USDC"),
        _market("DC", "D", "C"),
        _market("DUSDC", "D", "USDC"),
    )
    graph = build_market_graph(markets)
    routes = (
        *enumerate_triangular_routes(graph, anchor_asset="USDT"),
        *enumerate_triangular_routes(graph, anchor_asset="USDC"),
    )
    tickers = {market.symbol: _ticker(market.symbol) for market in markets}
    activities = {
        market.symbol: MarketActivity(
            market.symbol,
            Decimal("2000") if market.quote_asset == "USDC" else Decimal("1000"),
            1,
        )
        for market in markets
    }

    coverage = select_core_coverage(routes, tickers, activities, market_limit=6)

    assert len(coverage.seed_route_ids) == 2
    assert {
        next(route.assets[0] for route in routes if route.route_id == route_id)
        for route_id in coverage.seed_route_ids
    } == {"USDT", "USDC"}
    assert len(coverage.covered_route_ids) == 4
