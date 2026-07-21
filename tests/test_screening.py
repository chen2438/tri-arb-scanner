from decimal import Decimal

from tests.test_graph import _market
from tests.test_subscriptions import _route
from tri_arb.domain import build_market_graph, enumerate_triangular_routes
from tri_arb.domain.models import BookTicker
from tri_arb.scanner import (
    screen_routes,
    screen_routes_multi_anchor,
    screen_routes_with_diagnostics,
)


def _ticker(symbol: str, bid: str, ask: str) -> BookTicker:
    return BookTicker(
        symbol=symbol,
        bid_price=Decimal(bid),
        bid_quantity=Decimal("0.00000001"),
        ask_price=Decimal(ask),
        ask_quantity=Decimal("0.00000001"),
        received_time_ms=1,
    )


def test_screens_true_directions_with_fees_but_ignores_top_level_quantity() -> None:
    route = _route(1)
    tickers = {
        "A1USDT": _ticker("A1USDT", "1.99", "2"),
        "B1A1": _ticker("B1A1", "0.49", "0.5"),
        "B1USDT": _ticker("B1USDT", "1.1", "1.11"),
    }

    (candidate,) = screen_routes((route,), tickers, Decimal("100"))

    expected = Decimal("100") / Decimal("2") * Decimal("0.999")
    expected = expected / Decimal("0.5") * Decimal("0.999")
    expected = expected * Decimal("1.1") * Decimal("0.999")
    assert candidate.estimated_final_amount == expected
    assert candidate.estimated_return_bps == (expected / Decimal("100") - 1) * 10_000


def test_skips_incomplete_routes_and_sorts_deterministically() -> None:
    first = _route(1)
    second = _route(2)
    tickers = {
        symbol: _ticker(symbol, "1", "1.01")
        for route in (first, second)
        for symbol in (edge.market.symbol for edge in route.edges)
    }
    missing = dict(tickers)
    missing.pop(first.edges[0].market.symbol)

    candidates = screen_routes((second, first), tickers, Decimal("100"), limit=1)

    assert candidates[0].route.route_id == first.route_id
    assert screen_routes((first,), missing, Decimal("100")) == ()

    diagnostics = screen_routes_with_diagnostics((first, second), tickers, Decimal("100"), limit=1)
    assert diagnostics.total_route_count == 2
    assert diagnostics.priced_route_count == 2
    assert diagnostics.positive_route_count == 0
    assert len(diagnostics.candidates) == 1
    assert diagnostics.best_estimated_return_bps is not None


def test_fairly_shortlists_multiple_anchor_assets() -> None:
    usdt = _route(1)
    graph = build_market_graph(
        [
            _market("AUSDC", "A", "USDC"),
            _market("BA", "B", "A"),
            _market("BUSDC", "B", "USDC"),
        ]
    )
    usdc = enumerate_triangular_routes(graph, anchor_asset="USDC")[0]
    tickers = {
        symbol: _ticker(symbol, "1", "1.01")
        for route in (usdt, usdc)
        for symbol in (edge.market.symbol for edge in route.edges)
    }

    result = screen_routes_multi_anchor(
        (usdt, usdc),
        tickers,
        {"USDT": Decimal("100"), "USDC": Decimal("100")},
        limit=2,
    )

    assert {candidate.route.assets[0] for candidate in result.candidates} == {"USDT", "USDC"}
    assert result.total_route_count == 2
