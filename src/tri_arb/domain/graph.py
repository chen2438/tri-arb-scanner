"""Build the directed asset graph and deterministic USDT triangular routes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from tri_arb.domain.models import ConversionEdge, ConversionSide, MarketRules, TriangularRoute


def _market_edges(market: MarketRules) -> tuple[ConversionEdge, ...]:
    edges: list[ConversionEdge] = []
    if ConversionSide.BUY in market.allowed_sides:
        edges.append(
            ConversionEdge(
                market=market,
                from_asset=market.quote_asset,
                to_asset=market.base_asset,
                side=ConversionSide.BUY,
            )
        )
    if ConversionSide.SELL in market.allowed_sides:
        edges.append(
            ConversionEdge(
                market=market,
                from_asset=market.base_asset,
                to_asset=market.quote_asset,
                side=ConversionSide.SELL,
            )
        )
    return tuple(edges)


def build_market_graph(markets: Iterable[MarketRules]) -> Mapping[str, tuple[ConversionEdge, ...]]:
    by_symbol: dict[str, MarketRules] = {}
    graph: defaultdict[str, list[ConversionEdge]] = defaultdict(list)
    for market in markets:
        if market.symbol in by_symbol:
            raise ValueError(f"duplicate market symbol: {market.symbol}")
        by_symbol[market.symbol] = market
        for edge in _market_edges(market):
            graph[edge.from_asset].append(edge)
    return {
        asset: tuple(sorted(edges, key=lambda edge: (edge.to_asset, edge.market.symbol, edge.side)))
        for asset, edges in sorted(graph.items())
    }


def _route_id(edges: tuple[ConversionEdge, ConversionEdge, ConversionEdge]) -> str:
    return "|".join(f"{edge.market.symbol}:{edge.side}" for edge in edges)


def enumerate_triangular_routes(
    graph: Mapping[str, tuple[ConversionEdge, ...]],
    *,
    anchor_asset: str = "USDT",
) -> tuple[TriangularRoute, ...]:
    routes: dict[str, TriangularRoute] = {}
    for first in graph.get(anchor_asset, ()):
        asset_a = first.to_asset
        if asset_a == anchor_asset:
            continue
        for second in graph.get(asset_a, ()):
            asset_b = second.to_asset
            if asset_b in {anchor_asset, asset_a}:
                continue
            for third in graph.get(asset_b, ()):
                if third.to_asset != anchor_asset:
                    continue
                edges = (first, second, third)
                if len({edge.market.symbol for edge in edges}) != 3:
                    continue
                route_id = _route_id(edges)
                routes[route_id] = TriangularRoute(
                    route_id=route_id,
                    assets=(anchor_asset, asset_a, asset_b, anchor_asset),
                    edges=edges,
                )
    return tuple(routes[key] for key in sorted(routes))
