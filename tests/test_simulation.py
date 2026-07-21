from dataclasses import replace
from decimal import Decimal

import pytest

from tri_arb.domain import (
    BookLevel,
    ConversionSide,
    MarketRules,
    OrderBook,
    PriceLimit,
    PriceProtection,
    RejectReason,
    build_market_graph,
    confirmed_capacity,
    enumerate_triangular_routes,
    simulate_route,
)


def _market(
    symbol: str,
    base: str,
    quote: str,
    *,
    precision: int = 8,
    fee: str = "0",
    min_base: str = "0.00000001",
    min_quote: str = "0.000001",
    max_quote: str = "1000000",
    protection: str | None = None,
) -> MarketRules:
    return MarketRules(
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        base_asset_precision=precision,
        min_base_quantity=Decimal(min_base),
        min_quote_amount=Decimal(min_quote),
        max_quote_amount=Decimal(max_quote),
        taker_commission=Decimal(fee),
        allowed_sides=frozenset({ConversionSide.BUY, ConversionSide.SELL}),
        price_protection=(
            PriceProtection(Decimal(protection), Decimal(protection))
            if protection is not None
            else None
        ),
    )


def _level(price: str, quantity: str = "1000000") -> BookLevel:
    return BookLevel(Decimal(price), Decimal(quantity))


def _book(
    symbol: str,
    *,
    bids: tuple[BookLevel, ...],
    asks: tuple[BookLevel, ...],
) -> OrderBook:
    return OrderBook(
        symbol=symbol,
        bids=bids,
        asks=asks,
        version="1",
        source_time_ms=1_000_000,
        received_time_ms=1_000_010,
    )


def _route_and_books(*, fee: str = "0", precision: int = 8):
    markets = [
        _market("BTCUSDT", "BTC", "USDT", fee=fee, precision=precision),
        _market("ETHBTC", "ETH", "BTC", fee=fee, precision=precision),
        _market("ETHUSDT", "ETH", "USDT", fee=fee, precision=precision),
    ]
    graph = build_market_graph(markets)
    route = next(
        route
        for route in enumerate_triangular_routes(graph)
        if route.assets == ("USDT", "BTC", "ETH", "USDT")
    )
    books = {
        "BTCUSDT": _book(
            "BTCUSDT",
            bids=(_level("9990"),),
            asks=(_level("10000"),),
        ),
        "ETHBTC": _book(
            "ETHBTC",
            bids=(_level("0.049"),),
            asks=(_level("0.05"),),
        ),
        "ETHUSDT": _book(
            "ETHUSDT",
            bids=(_level("510"),),
            asks=(_level("511"),),
        ),
    }
    return route, books


def test_simulates_profitable_three_leg_route_and_buffer() -> None:
    route, books = _route_and_books()

    outcome = simulate_route(route, books, Decimal("100"), safety_buffer_bps=Decimal("5"))

    assert outcome.accepted
    assert outcome.simulation is not None
    assert outcome.simulation.final_amount == Decimal("102.00")
    assert outcome.simulation.gross_return_bps == Decimal("200.00")
    assert outcome.simulation.modeled_return_bps == Decimal("200.00")
    assert outcome.simulation.net_return_bps == Decimal("195.00")
    assert outcome.simulation.estimated_profit == Decimal("1.9500")


def test_applies_each_leg_fee_to_received_asset() -> None:
    route, books = _route_and_books(fee="0.001")

    outcome = simulate_route(route, books, Decimal("100"), safety_buffer_bps=Decimal("0"))

    assert outcome.simulation is not None
    first, second, third = outcome.simulation.legs
    assert first.fee_amount == Decimal("0.00001000")
    assert second.fee_amount == Decimal("0.00019980")
    assert third.fee_amount == Decimal("0.10179610200")
    assert outcome.simulation.final_amount == Decimal("101.69430589800")


def test_consumes_multiple_levels_and_records_average_price() -> None:
    route, books = _route_and_books()
    books["BTCUSDT"] = _book(
        "BTCUSDT",
        bids=(_level("9990"),),
        asks=(_level("10000", "0.005"), _level("10100", "1")),
    )

    outcome = simulate_route(route, books, Decimal("100"), safety_buffer_bps=Decimal("0"))

    assert outcome.simulation is not None
    first = outcome.simulation.legs[0]
    assert first.levels_consumed == 2
    assert first.average_price > Decimal("10000")
    assert first.average_price < Decimal("10100")


def test_rejects_book_levels_outside_price_protection_and_requires_reference() -> None:
    route, books = _route_and_books()
    protected = _market("BTCUSDT", "BTC", "USDT", protection="0.2")
    route = type(route)(
        route.route_id,
        route.assets,
        (type(route.edges[0])(protected, "USDT", "BTC", ConversionSide.BUY), *route.edges[1:]),
    )

    missing = simulate_route(route, books, Decimal("100"), safety_buffer_bps=Decimal("0"))
    blocked = simulate_route(
        route,
        books,
        Decimal("100"),
        safety_buffer_bps=Decimal("0"),
        reference_prices={"BTCUSDT": Decimal("8000")},
    )
    allowed = simulate_route(
        route,
        books,
        Decimal("100"),
        safety_buffer_bps=Decimal("0"),
        reference_prices={"BTCUSDT": Decimal("9000")},
    )

    assert missing.reject_reasons == (RejectReason.MISSING_PRICE_REFERENCE,)
    assert blocked.reject_reasons == (RejectReason.PRICE_PROTECTION,)
    assert allowed.simulation is not None
    assert allowed.simulation.legs[0].price_protection_limit == Decimal("10800.0")


def test_applies_exchange_computed_price_limits_and_requires_current_input() -> None:
    route, books = _route_and_books()
    first = replace(
        route.edges[0],
        market=replace(route.edges[0].market, requires_explicit_price_limit=True),
    )
    route = replace(route, edges=(first, *route.edges[1:]))
    missing = simulate_route(route, books, Decimal("100"))
    blocked = simulate_route(
        route,
        books,
        Decimal("100"),
        price_limits={
            "BTCUSDT": PriceLimit(
                "BTCUSDT", True, Decimal("9999"), Decimal("9000"), 1_000_000, 1_000_010
            )
        },
    )
    allowed = simulate_route(
        route,
        books,
        Decimal("100"),
        price_limits={
            "BTCUSDT": PriceLimit(
                "BTCUSDT", True, Decimal("10000"), Decimal("9000"), 1_000_000, 1_000_010
            )
        },
    )

    assert missing.reject_reasons == (RejectReason.MISSING_PRICE_LIMIT,)
    assert blocked.reject_reasons == (RejectReason.PRICE_PROTECTION,)
    assert allowed.simulation is not None
    assert allowed.simulation.legs[0].price_protection_limit == Decimal("10000")


def test_checks_sell_floor_and_stops_capacity_at_protected_depth() -> None:
    route, books = _route_and_books()
    last = route.edges[2]
    protected_last = replace(
        last,
        market=replace(
            last.market,
            price_protection=PriceProtection(Decimal("0.2"), Decimal("0.2")),
        ),
    )
    sell_route = replace(route, edges=(*route.edges[:2], protected_last))

    blocked = simulate_route(
        sell_route,
        books,
        Decimal("100"),
        reference_prices={"ETHUSDT": Decimal("700")},
    )
    assert blocked.reject_reasons == (RejectReason.PRICE_PROTECTION,)

    first = route.edges[0]
    buy_route = replace(
        route,
        edges=(
            replace(
                first,
                market=replace(
                    first.market,
                    price_protection=PriceProtection(Decimal("0.2"), Decimal("0.2")),
                ),
            ),
            *route.edges[1:],
        ),
    )
    books["BTCUSDT"] = _book(
        "BTCUSDT",
        bids=(_level("9990"),),
        asks=(_level("10000", "0.005"), _level("11000", "1")),
    )
    capacity = confirmed_capacity(
        buy_route,
        books,
        Decimal("10"),
        reference_prices={"BTCUSDT": Decimal("9000")},
    )

    assert Decimal("49.99") <= capacity <= Decimal("50.00")


def test_rounds_base_quantity_down_and_records_unspent_dust() -> None:
    route, books = _route_and_books(precision=3)

    outcome = simulate_route(route, books, Decimal("105"), safety_buffer_bps=Decimal("0"))

    assert outcome.simulation is not None
    first = outcome.simulation.legs[0]
    assert first.output_amount == Decimal("0.010")
    assert first.dust_amount == Decimal("5.000")


@pytest.mark.parametrize(
    ("start", "reason"),
    [
        ("0", RejectReason.NON_POSITIVE_INPUT),
        ("-1", RejectReason.NON_POSITIVE_INPUT),
        ("NaN", RejectReason.NON_POSITIVE_INPUT),
    ],
)
def test_rejects_non_positive_or_non_finite_input(start: str, reason: RejectReason) -> None:
    route, books = _route_and_books()

    outcome = simulate_route(route, books, Decimal(start))

    assert outcome.reject_reasons == (reason,)


def test_rejects_missing_or_incomplete_books() -> None:
    route, books = _route_and_books()
    del books["ETHBTC"]

    missing = simulate_route(route, books, Decimal("100"))
    assert missing.reject_reasons == (RejectReason.MISSING_BOOK,)

    books["ETHBTC"] = _book("ETHBTC", bids=(), asks=())
    invalid = simulate_route(route, books, Decimal("100"))
    assert invalid.reject_reasons == (RejectReason.INVALID_BOOK,)


def test_rejects_insufficient_twenty_level_capacity() -> None:
    route, books = _route_and_books()
    books["BTCUSDT"] = _book(
        "BTCUSDT",
        bids=(_level("9990"),),
        asks=(_level("10000", "0.001"),),
    )

    outcome = simulate_route(route, books, Decimal("100"))

    assert outcome.reject_reasons == (RejectReason.INSUFFICIENT_DEPTH,)


def test_rejects_minimum_and_maximum_order_rules() -> None:
    route, books = _route_and_books()
    first_market = route.edges[0].market
    restrictive = _market(
        first_market.symbol,
        first_market.base_asset,
        first_market.quote_asset,
        min_quote="101",
        max_quote="1000",
    )
    route_with_minimum = type(route)(
        route_id=route.route_id,
        assets=route.assets,
        edges=(
            type(route.edges[0])(
                restrictive,
                route.edges[0].from_asset,
                route.edges[0].to_asset,
                route.edges[0].side,
            ),
            route.edges[1],
            route.edges[2],
        ),
    )
    below = simulate_route(route_with_minimum, books, Decimal("100"))
    assert below.reject_reasons == (RejectReason.BELOW_MIN_QUOTE,)

    capped = _market(
        first_market.symbol,
        first_market.base_asset,
        first_market.quote_asset,
        min_quote="1",
        max_quote="99",
    )
    route_with_cap = type(route)(
        route_id=route.route_id,
        assets=route.assets,
        edges=(
            type(route.edges[0])(
                capped,
                route.edges[0].from_asset,
                route.edges[0].to_asset,
                route.edges[0].side,
            ),
            route.edges[1],
            route.edges[2],
        ),
    )
    above = simulate_route(route_with_cap, books, Decimal("100"))
    assert above.reject_reasons == (RejectReason.ABOVE_MAX_QUOTE,)


def test_finds_largest_confirmed_capacity_to_cent_precision() -> None:
    route, books = _route_and_books()
    books["BTCUSDT"] = _book(
        "BTCUSDT",
        bids=(_level("9990"),),
        asks=(_level("10000", "0.02"),),
    )

    capacity = confirmed_capacity(route, books, Decimal("100"))

    assert capacity == Decimal("200.00")


def test_order_book_rejects_unsorted_or_crossed_input() -> None:
    with pytest.raises(ValueError, match="bids must be sorted"):
        _book(
            "BTCUSDT",
            bids=(_level("99"), _level("100")),
            asks=(_level("101"),),
        )

    route, books = _route_and_books()
    books["BTCUSDT"] = _book(
        "BTCUSDT",
        bids=(_level("10001"),),
        asks=(_level("10000"),),
    )
    outcome = simulate_route(route, books, Decimal("100"))
    assert outcome.reject_reasons == (RejectReason.INVALID_BOOK,)


def test_order_book_rejects_duplicate_price_levels() -> None:
    with pytest.raises(ValueError, match="bid prices must be unique"):
        _book(
            "BTCUSDT",
            bids=(_level("99"), _level("99")),
            asks=(_level("101"),),
        )
