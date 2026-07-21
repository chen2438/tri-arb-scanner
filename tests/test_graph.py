from decimal import Decimal

import pytest

from tri_arb.domain import (
    ConversionSide,
    MarketRules,
    build_market_graph,
    enumerate_triangular_routes,
)


def _market(
    symbol: str,
    base: str,
    quote: str,
    sides: frozenset[ConversionSide] | None = None,
) -> MarketRules:
    return MarketRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        base_asset_precision=8,
        min_base_quantity=Decimal("0.00000001"),
        min_quote_amount=Decimal("0.01"),
        max_quote_amount=Decimal("1000000"),
        taker_commission=Decimal("0.001"),
        allowed_sides=sides or frozenset({ConversionSide.BUY, ConversionSide.SELL}),
    )


def test_enumerates_both_real_execution_directions_without_rotation_duplicates() -> None:
    graph = build_market_graph(
        [
            _market("BTCUSDT", "BTC", "USDT"),
            _market("ETHBTC", "ETH", "BTC"),
            _market("ETHUSDT", "ETH", "USDT"),
        ]
    )

    routes = enumerate_triangular_routes(graph)

    assert [route.assets for route in routes] == [
        ("USDT", "BTC", "ETH", "USDT"),
        ("USDT", "ETH", "BTC", "USDT"),
    ]
    assert len({route.route_id for route in routes}) == 2


def test_one_sided_market_removes_only_the_disallowed_route_direction() -> None:
    graph = build_market_graph(
        [
            _market("BTCUSDT", "BTC", "USDT"),
            _market(
                "ETHBTC",
                "ETH",
                "BTC",
                frozenset({ConversionSide.BUY}),
            ),
            _market("ETHUSDT", "ETH", "USDT"),
        ]
    )

    routes = enumerate_triangular_routes(graph)

    assert [route.assets for route in routes] == [("USDT", "BTC", "ETH", "USDT")]


def test_excludes_non_anchor_cycles_and_repeated_markets() -> None:
    graph = build_market_graph(
        [
            _market("ETHBTC", "ETH", "BTC"),
            _market("MXETH", "MX", "ETH"),
            _market("MXBTC", "MX", "BTC"),
        ]
    )

    assert enumerate_triangular_routes(graph) == ()


def test_rejects_duplicate_market_symbols() -> None:
    market = _market("BTCUSDT", "BTC", "USDT")
    with pytest.raises(ValueError, match="duplicate market symbol"):
        build_market_graph([market, market])
