"""Stable core-depth selection from route coverage, volume, and spread quality."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from tri_arb.domain.models import BookTicker, MarketActivity, TriangularRoute

CORE_DEPTH_MARKET_LIMIT = 30
BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class CoreCoverage:
    symbols: tuple[str, ...]
    seed_route_ids: tuple[str, ...]
    covered_route_ids: tuple[str, ...]


def _route_symbols(route: TriangularRoute) -> frozenset[str]:
    return frozenset(edge.market.symbol for edge in route.edges)


def select_core_coverage(
    routes: Sequence[TriangularRoute],
    tickers: Mapping[str, BookTicker],
    activities: Mapping[str, MarketActivity],
    *,
    market_limit: int = CORE_DEPTH_MARKET_LIMIT,
) -> CoreCoverage:
    if market_limit < 0:
        raise ValueError("core market limit cannot be negative")
    if market_limit and market_limit < 3:
        raise ValueError("core market limit must fit at least one complete route")
    route_symbols = {route.route_id: _route_symbols(route) for route in routes}
    routes_by_symbol: defaultdict[str, set[str]] = defaultdict(set)
    for route_id, symbols in route_symbols.items():
        for symbol in symbols:
            routes_by_symbol[symbol].add(route_id)
    coverage = Counter(symbol for symbols in route_symbols.values() for symbol in symbols)
    markets = {edge.market.symbol: edge.market for route in routes for edge in route.edges}
    by_quote: defaultdict[str, list[tuple[Decimal, str]]] = defaultdict(list)
    for symbol, market in markets.items():
        activity = activities.get(symbol)
        by_quote[market.quote_asset].append(
            (activity.quote_volume if activity is not None else Decimal(0), symbol)
        )
    volume_score: dict[str, Decimal] = {}
    for values in by_quote.values():
        ordered = sorted(values)
        denominator = Decimal(max(1, len(ordered)))
        for rank, (_volume, symbol) in enumerate(ordered, start=1):
            volume_score[symbol] = Decimal(rank) / denominator

    spread_bps: dict[str, Decimal] = {}
    for symbol in markets:
        ticker = tickers.get(symbol)
        if ticker is None:
            spread_bps[symbol] = Decimal("1000000")
            continue
        midpoint = (ticker.ask_price + ticker.bid_price) / 2
        spread_bps[symbol] = (ticker.ask_price - ticker.bid_price) / midpoint * BPS

    def quality(route: TriangularRoute) -> tuple[int, Decimal, Decimal]:
        symbols = route_symbols[route.route_id]
        return (
            sum(coverage[symbol] for symbol in symbols),
            sum((volume_score.get(symbol, Decimal(0)) for symbol in symbols), Decimal(0)),
            -sum((spread_bps[symbol] for symbol in symbols), Decimal(0)),
        )

    selected_symbols: set[str] = set()
    seeds: list[str] = []
    anchors = tuple(sorted({route.assets[0] for route in routes}))
    for anchor in anchors:
        candidates = [
            route
            for route in routes
            if route.assets[0] == anchor
            and len(selected_symbols | set(route_symbols[route.route_id])) <= market_limit
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda route: (
                -quality(route)[0],
                -quality(route)[1],
                -quality(route)[2],
                route.route_id,
            )
        )
        chosen = candidates[0]
        selected_symbols.update(route_symbols[chosen.route_id])
        seeds.append(chosen.route_id)

    while True:
        candidates = [
            route
            for route in routes
            if route_symbols[route.route_id] - selected_symbols
            and len(selected_symbols | set(route_symbols[route.route_id])) <= market_limit
        ]
        if not candidates:
            break

        def greedy_key(route: TriangularRoute) -> tuple[int, int, Decimal, Decimal, str]:
            proposed = selected_symbols | set(route_symbols[route.route_id])
            newly_touched = set().union(
                *(routes_by_symbol[symbol] for symbol in proposed - selected_symbols)
            )
            completed = sum(route_symbols[route_id] <= proposed for route_id in newly_touched)
            route_quality = quality(route)
            return (
                -completed,
                -route_quality[0],
                -route_quality[1],
                -route_quality[2],
                route.route_id,
            )

        candidates.sort(key=greedy_key)
        selected_symbols.update(route_symbols[candidates[0].route_id])
        if len(selected_symbols) == market_limit:
            break

    covered = tuple(
        sorted(
            route_id for route_id, symbols in route_symbols.items() if symbols <= selected_symbols
        )
    )
    return CoreCoverage(tuple(sorted(selected_symbols)), tuple(seeds), covered)
