"""Fast Decimal top-of-book screening that never claims depth confirmation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException

from tri_arb.domain.models import BookTicker, ConversionSide, TriangularRoute

BPS = Decimal("10000")
ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class BroadCandidate:
    route: TriangularRoute
    start_amount: Decimal
    estimated_final_amount: Decimal
    estimated_return_bps: Decimal


@dataclass(frozen=True, slots=True)
class BroadScreenResult:
    candidates: tuple[BroadCandidate, ...]
    total_route_count: int
    priced_route_count: int
    positive_route_count: int
    best_estimated_return_bps: Decimal | None


def _estimate_route(
    route: TriangularRoute,
    tickers: Mapping[str, BookTicker],
    start_amount: Decimal,
) -> BroadCandidate | None:
    amount = start_amount
    try:
        for edge in route.edges:
            ticker = tickers.get(edge.market.symbol)
            if ticker is None:
                return None
            if edge.side is ConversionSide.BUY:
                amount /= ticker.ask_price
            else:
                amount *= ticker.bid_price
            amount *= ONE - edge.market.taker_commission
        return BroadCandidate(
            route=route,
            start_amount=start_amount,
            estimated_final_amount=amount,
            estimated_return_bps=(amount / start_amount - ONE) * BPS,
        )
    except (DecimalException, OverflowError, ZeroDivisionError):
        return None


def screen_routes(
    routes: Sequence[TriangularRoute],
    tickers: Mapping[str, BookTicker],
    start_amount: Decimal,
    *,
    limit: int = 20,
) -> tuple[BroadCandidate, ...]:
    return screen_routes_with_diagnostics(routes, tickers, start_amount, limit=limit).candidates


def screen_routes_with_diagnostics(
    routes: Sequence[TriangularRoute],
    tickers: Mapping[str, BookTicker],
    start_amount: Decimal,
    *,
    limit: int = 20,
) -> BroadScreenResult:
    if not start_amount.is_finite() or start_amount <= 0:
        raise ValueError("broad-screen start amount must be finite and positive")
    if limit < 0:
        raise ValueError("broad-screen limit cannot be negative")
    candidates = [
        candidate
        for route in routes
        if (candidate := _estimate_route(route, tickers, start_amount)) is not None
    ]
    candidates.sort(key=lambda item: (-item.estimated_return_bps, item.route.route_id))
    return BroadScreenResult(
        candidates=tuple(candidates[:limit]),
        total_route_count=len(routes),
        priced_route_count=len(candidates),
        positive_route_count=sum(candidate.estimated_return_bps > 0 for candidate in candidates),
        best_estimated_return_bps=(candidates[0].estimated_return_bps if candidates else None),
    )


def screen_routes_multi_anchor(
    routes: Sequence[TriangularRoute],
    tickers: Mapping[str, BookTicker],
    start_amounts: Mapping[str, Decimal],
    *,
    limit: int = 20,
) -> BroadScreenResult:
    if limit < 0:
        raise ValueError("broad-screen limit cannot be negative")
    anchors = tuple(sorted({route.assets[0] for route in routes}))
    unknown = set(anchors) - set(start_amounts)
    if unknown:
        raise ValueError(f"missing start amount for anchor: {', '.join(sorted(unknown))}")
    results = {
        anchor: screen_routes_with_diagnostics(
            tuple(route for route in routes if route.assets[0] == anchor),
            tickers,
            start_amounts[anchor],
            limit=limit,
        )
        for anchor in anchors
    }
    selected: list[BroadCandidate] = []
    used: set[str] = set()
    if anchors and limit:
        base_quota, remainder = divmod(limit, len(anchors))
        for index, anchor in enumerate(anchors):
            quota = base_quota + (1 if index < remainder else 0)
            for candidate in results[anchor].candidates[:quota]:
                selected.append(candidate)
                used.add(candidate.route.route_id)
        remaining = sorted(
            (
                candidate
                for result in results.values()
                for candidate in result.candidates
                if candidate.route.route_id not in used
            ),
            key=lambda candidate: (-candidate.estimated_return_bps, candidate.route.route_id),
        )
        selected.extend(remaining[: limit - len(selected)])
    selected.sort(key=lambda candidate: (-candidate.estimated_return_bps, candidate.route.route_id))
    best_values = tuple(
        result.best_estimated_return_bps
        for result in results.values()
        if result.best_estimated_return_bps is not None
    )
    return BroadScreenResult(
        candidates=tuple(selected),
        total_route_count=sum(result.total_route_count for result in results.values()),
        priced_route_count=sum(result.priced_route_count for result in results.values()),
        positive_route_count=sum(result.positive_route_count for result in results.values()),
        best_estimated_return_bps=max(best_values, default=None),
    )
