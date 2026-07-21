from dataclasses import replace
from decimal import Decimal

from tests.test_simulation import _route_and_books
from tri_arb.domain.models import PriceLimit, PriceProtection, PriceReference
from tri_arb.exchange.mexc import (
    DepthUpdate,
    MarketLease,
    SubscriptionPlan,
    WebSocketState,
    WebSocketStatus,
)
from tri_arb.scanner import BroadCandidate, confirm_candidate


def _inputs(*, generation: int = 1):
    route, books = _route_and_books()
    symbols = tuple(sorted(books))
    candidate = BroadCandidate(route, Decimal("100"), Decimal("102"), Decimal("200"))
    updates = {symbol: DepthUpdate(book, 0, generation, 1) for symbol, book in books.items()}
    plan = SubscriptionPlan(
        leases=tuple(MarketLease(symbol, 1) for symbol in symbols),
        selected_route_ids=(route.route_id,),
        shards=(symbols, ()),
    )
    statuses = (
        WebSocketStatus(0, WebSocketState.CONNECTED, 1, symbols),
        WebSocketStatus(1, WebSocketState.IDLE, 0, ()),
    )
    return candidate, updates, plan, statuses


def test_confirms_current_three_leg_depth_and_capacity() -> None:
    candidate, updates, plan, statuses = _inputs()

    outcome = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        safety_buffer_bps=Decimal("5"),
    )

    assert outcome.accepted
    assert outcome.simulation is not None
    assert outcome.simulation.net_return_bps == Decimal("195.00")
    assert outcome.confirmed_capacity_usdt is not None
    assert outcome.confirmed_capacity_usdt >= Decimal("100")
    assert outcome.timing is not None
    assert outcome.timing.market_age_ms == 100


def test_rejects_missing_or_stale_connection_generation() -> None:
    candidate, updates, plan, statuses = _inputs(generation=0)

    stale_generation = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        safety_buffer_bps=Decimal("5"),
    )
    updates.pop(candidate.route.edges[0].market.symbol)
    missing = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        safety_buffer_bps=Decimal("5"),
    )

    assert stale_generation.reject_reasons == ("stale_generation",)
    assert missing.reject_reasons == ("missing_current_depth", "stale_generation")


def test_rejects_stale_or_skewed_depth_before_simulation() -> None:
    candidate, updates, plan, statuses = _inputs()

    stale = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_002_001,
        safety_buffer_bps=Decimal("5"),
    )
    skewed_symbol = candidate.route.edges[0].market.symbol
    old = updates[skewed_symbol]
    skewed_book = type(old.book)(
        symbol=old.book.symbol,
        bids=old.book.bids,
        asks=old.book.asks,
        version=old.book.version,
        source_time_ms=998_999,
        received_time_ms=old.book.received_time_ms,
    )
    updates[skewed_symbol] = DepthUpdate(skewed_book, 0, 1, 1)
    skewed = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_000,
        safety_buffer_bps=Decimal("5"),
    )

    assert stale.reject_reasons == ("stale_depth",)
    assert skewed.reject_reasons == ("leg_skew",)


def test_fails_closed_for_missing_or_stale_protection_reference() -> None:
    candidate, updates, plan, statuses = _inputs()
    first = candidate.route.edges[0]
    protected_market = replace(
        first.market,
        price_protection=PriceProtection(Decimal("0.2"), Decimal("0.2")),
    )
    protected_edge = replace(first, market=protected_market)
    route = replace(candidate.route, edges=(protected_edge, *candidate.route.edges[1:]))
    candidate = replace(candidate, route=route)

    missing = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        local_time_ms=1_000_100,
        safety_buffer_bps=Decimal("5"),
    )
    stale = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        local_time_ms=1_000_100,
        price_references={
            protected_market.symbol: PriceReference(
                protected_market.symbol, Decimal("9000"), 5, 900_000
            )
        },
        safety_buffer_bps=Decimal("5"),
    )

    assert missing.reject_reasons == ("missing_price_reference",)
    assert stale.reject_reasons == ("stale_price_reference",)


def test_fails_closed_for_missing_stale_or_blocking_explicit_price_limit() -> None:
    candidate, updates, plan, statuses = _inputs()
    first = candidate.route.edges[0]
    limited_edge = replace(
        first,
        market=replace(first.market, requires_explicit_price_limit=True),
    )
    candidate = replace(
        candidate,
        route=replace(candidate.route, edges=(limited_edge, *candidate.route.edges[1:])),
    )
    missing = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        local_time_ms=1_000_100,
        safety_buffer_bps=Decimal("5"),
    )
    stale = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        local_time_ms=1_000_100,
        price_limits={
            first.market.symbol: PriceLimit(
                first.market.symbol,
                True,
                Decimal("11000"),
                Decimal("9000"),
                900_000,
                900_000,
            )
        },
        safety_buffer_bps=Decimal("5"),
    )
    blocked = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        local_time_ms=1_000_100,
        price_limits={
            first.market.symbol: PriceLimit(
                first.market.symbol,
                True,
                Decimal("9999"),
                Decimal("9000"),
                1_000_000,
                1_000_050,
            )
        },
        safety_buffer_bps=Decimal("5"),
    )

    assert missing.reject_reasons == ("missing_price_limit",)
    assert stale.reject_reasons == ("stale_price_limit",)
    assert blocked.reject_reasons == ("price_protection",)
