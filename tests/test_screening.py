from decimal import Decimal

from tests.test_subscriptions import _route
from tri_arb.domain.models import BookTicker
from tri_arb.scanner import screen_routes


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
