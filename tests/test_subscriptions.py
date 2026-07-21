from decimal import Decimal

from tri_arb.domain.models import (
    ConversionEdge,
    ConversionSide,
    MarketRules,
    TriangularRoute,
)
from tri_arb.exchange.mexc import MarketLease, reconcile_subscriptions


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


def _route(index: int) -> TriangularRoute:
    asset_a = f"A{index}"
    asset_b = f"B{index}"
    first = _market(f"A{index}USDT", asset_a, "USDT")
    second = _market(f"B{index}A{index}", asset_b, asset_a)
    third = _market(f"B{index}USDT", asset_b, "USDT")
    edges = (
        ConversionEdge(first, "USDT", asset_a, ConversionSide.BUY),
        ConversionEdge(second, asset_a, asset_b, ConversionSide.BUY),
        ConversionEdge(third, asset_b, "USDT", ConversionSide.SELL),
    )
    return TriangularRoute(
        route_id=f"route-{index:02d}",
        assets=("USDT", asset_a, asset_b, "USDT"),
        edges=edges,
    )


def test_limits_shortlist_to_twenty_complete_routes_and_two_thirty_market_shards() -> None:
    routes = tuple(_route(index) for index in range(21))

    plan = reconcile_subscriptions(routes, (), now_ms=10_000)

    assert plan.selected_route_ids == tuple(f"route-{index:02d}" for index in range(20))
    assert len(plan.symbols) == 60
    assert len(plan.shards[0]) == 30
    assert len(plan.shards[1]) == 30
    assert tuple(sorted(plan.symbols)) == plan.shards[0] + plan.shards[1]


def test_retains_young_market_then_removes_it_after_minimum_residence() -> None:
    routes = tuple(_route(index) for index in range(20))
    lease = MarketLease("OLDUSDT", subscribed_at_ms=1_000)

    young_plan = reconcile_subscriptions(routes, (lease,), now_ms=15_999)
    expired_plan = reconcile_subscriptions(routes, young_plan.leases, now_ms=16_000)

    assert "OLDUSDT" in young_plan.symbols
    assert len(young_plan.selected_route_ids) == 19
    assert "OLDUSDT" not in expired_plan.symbols
    assert len(expired_plan.selected_route_ids) == 20


def test_reconciliation_is_deterministic_and_deduplicates_ranked_routes() -> None:
    route = _route(1)

    first = reconcile_subscriptions((route, route), (), now_ms=100)
    second = reconcile_subscriptions((route, route), (), now_ms=100)

    assert first == second
    assert first.selected_route_ids == (route.route_id,)
