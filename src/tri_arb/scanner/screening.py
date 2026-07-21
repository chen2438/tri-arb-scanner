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
    return tuple(candidates[:limit])
