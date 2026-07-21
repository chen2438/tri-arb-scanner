"""Conservative Decimal-based three-leg order-book simulation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, Decimal, DecimalException

from tri_arb.domain.models import (
    ZERO,
    BookLevel,
    ConversionEdge,
    ConversionSide,
    LegSimulation,
    OrderBook,
    RejectReason,
    RouteSimulation,
    SimulationOutcome,
    TriangularRoute,
)

BPS = Decimal("10000")
CAPACITY_QUANTUM = Decimal("0.01")


class _Rejected(Exception):
    def __init__(self, reason: RejectReason) -> None:
        self.reason = reason


def _floor(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_DOWN)


def _validate_book(book: OrderBook) -> None:
    if not book.bids or not book.asks:
        raise _Rejected(RejectReason.INVALID_BOOK)
    if book.bids[0].price >= book.asks[0].price:
        raise _Rejected(RejectReason.INVALID_BOOK)


def _quote_for_base(quantity: Decimal, levels: Sequence[BookLevel]) -> tuple[Decimal, int]:
    remaining = quantity
    quote = ZERO
    consumed = 0
    for level in levels:
        take = min(remaining, level.quantity)
        if take > ZERO:
            quote += take * level.price
            remaining -= take
            consumed += 1
        if remaining == ZERO:
            break
    if remaining > ZERO:
        raise _Rejected(RejectReason.INSUFFICIENT_DEPTH)
    return quote, consumed


def _base_for_quote(quote: Decimal, levels: Sequence[BookLevel]) -> Decimal:
    remaining = quote
    base = ZERO
    for level in levels:
        level_quote = level.price * level.quantity
        spend = min(remaining, level_quote)
        if spend > ZERO:
            base += spend / level.price
            remaining -= spend
        if remaining == ZERO:
            return base
    raise _Rejected(RejectReason.INSUFFICIENT_DEPTH)


def _check_order_rules(edge: ConversionEdge, base_quantity: Decimal, quote_amount: Decimal) -> None:
    rules = edge.market
    if base_quantity < rules.min_base_quantity:
        raise _Rejected(RejectReason.BELOW_MIN_BASE)
    if quote_amount < rules.min_quote_amount:
        raise _Rejected(RejectReason.BELOW_MIN_QUOTE)
    if quote_amount > rules.max_quote_amount:
        raise _Rejected(RejectReason.ABOVE_MAX_QUOTE)


def _simulate_buy(
    edge: ConversionEdge,
    book: OrderBook,
    input_amount: Decimal,
    reference_price: Decimal | None,
) -> LegSimulation:
    raw_base = _base_for_quote(input_amount, book.asks)
    base_quantity = _floor(raw_base, edge.market.base_quantum)
    if base_quantity <= ZERO:
        raise _Rejected(RejectReason.BELOW_MIN_BASE)
    quote_spent, levels_consumed = _quote_for_base(base_quantity, book.asks)
    _check_order_rules(edge, base_quantity, quote_spent)
    protection_limit = None
    if edge.market.price_protection is not None:
        if reference_price is None or not reference_price.is_finite() or reference_price <= ZERO:
            raise _Rejected(RejectReason.MISSING_PRICE_REFERENCE)
        protection_limit = reference_price * (
            Decimal(1) + edge.market.price_protection.max_buy_deviation
        )
        if book.asks[levels_consumed - 1].price > protection_limit:
            raise _Rejected(RejectReason.PRICE_PROTECTION)
    fee = base_quantity * edge.market.taker_commission
    output = base_quantity - fee
    return LegSimulation(
        symbol=edge.market.symbol,
        side=edge.side,
        from_asset=edge.from_asset,
        to_asset=edge.to_asset,
        input_amount=input_amount,
        output_amount=output,
        average_price=quote_spent / base_quantity,
        fee_rate=edge.market.taker_commission,
        fee_amount=fee,
        dust_amount=input_amount - quote_spent,
        levels_consumed=levels_consumed,
        book_version=book.version,
        source_time_ms=book.source_time_ms,
        received_time_ms=book.received_time_ms,
        price_reference=reference_price,
        price_protection_limit=protection_limit,
    )


def _simulate_sell(
    edge: ConversionEdge,
    book: OrderBook,
    input_amount: Decimal,
    reference_price: Decimal | None,
) -> LegSimulation:
    base_quantity = _floor(input_amount, edge.market.base_quantum)
    if base_quantity <= ZERO:
        raise _Rejected(RejectReason.BELOW_MIN_BASE)
    quote_received, levels_consumed = _quote_for_base(base_quantity, book.bids)
    _check_order_rules(edge, base_quantity, quote_received)
    protection_limit = None
    if edge.market.price_protection is not None:
        if reference_price is None or not reference_price.is_finite() or reference_price <= ZERO:
            raise _Rejected(RejectReason.MISSING_PRICE_REFERENCE)
        protection_limit = reference_price * (
            Decimal(1) - edge.market.price_protection.max_sell_deviation
        )
        if book.bids[levels_consumed - 1].price < protection_limit:
            raise _Rejected(RejectReason.PRICE_PROTECTION)
    fee = quote_received * edge.market.taker_commission
    output = quote_received - fee
    return LegSimulation(
        symbol=edge.market.symbol,
        side=edge.side,
        from_asset=edge.from_asset,
        to_asset=edge.to_asset,
        input_amount=input_amount,
        output_amount=output,
        average_price=quote_received / base_quantity,
        fee_rate=edge.market.taker_commission,
        fee_amount=fee,
        dust_amount=input_amount - base_quantity,
        levels_consumed=levels_consumed,
        book_version=book.version,
        source_time_ms=book.source_time_ms,
        received_time_ms=book.received_time_ms,
        price_reference=reference_price,
        price_protection_limit=protection_limit,
    )


def _gross_final_amount(
    route: TriangularRoute,
    books: Mapping[str, OrderBook],
    start_amount: Decimal,
) -> Decimal:
    amount = start_amount
    for edge in route.edges:
        book = books[edge.market.symbol]
        price = book.asks[0].price if edge.side is ConversionSide.BUY else book.bids[0].price
        amount = amount / price if edge.side is ConversionSide.BUY else amount * price
    return amount


def simulate_route(
    route: TriangularRoute,
    books: Mapping[str, OrderBook],
    start_amount: Decimal,
    *,
    safety_buffer_bps: Decimal = Decimal("5"),
    reference_prices: Mapping[str, Decimal] | None = None,
) -> SimulationOutcome:
    if not start_amount.is_finite() or start_amount <= ZERO:
        return SimulationOutcome(None, (RejectReason.NON_POSITIVE_INPUT,))
    if not safety_buffer_bps.is_finite() or safety_buffer_bps < ZERO:
        return SimulationOutcome(None, (RejectReason.INVALID_RULE,))

    route_books: dict[str, OrderBook] = {}
    reasons: list[RejectReason] = []
    for edge in route.edges:
        book = books.get(edge.market.symbol)
        if book is None:
            reasons.append(RejectReason.MISSING_BOOK)
            continue
        if book.symbol != edge.market.symbol:
            reasons.append(RejectReason.INVALID_BOOK)
            continue
        try:
            _validate_book(book)
        except _Rejected as error:
            reasons.append(error.reason)
            continue
        route_books[edge.market.symbol] = book
    if reasons:
        return SimulationOutcome(None, tuple(dict.fromkeys(reasons)))

    try:
        amount = start_amount
        legs: list[LegSimulation] = []
        for edge in route.edges:
            book = route_books[edge.market.symbol]
            reference_price = (reference_prices or {}).get(edge.market.symbol)
            leg = (
                _simulate_buy(edge, book, amount, reference_price)
                if edge.side is ConversionSide.BUY
                else _simulate_sell(edge, book, amount, reference_price)
            )
            legs.append(leg)
            amount = leg.output_amount
        gross_final = _gross_final_amount(route, route_books, start_amount)
    except _Rejected as error:
        return SimulationOutcome(None, (error.reason,))
    except (DecimalException, OverflowError, ZeroDivisionError):
        return SimulationOutcome(None, (RejectReason.INVALID_RULE,))

    gross_return_bps = (gross_final / start_amount - Decimal(1)) * BPS
    modeled_return_bps = (amount / start_amount - Decimal(1)) * BPS
    net_return_bps = modeled_return_bps - safety_buffer_bps
    simulation = RouteSimulation(
        route=route,
        start_amount=start_amount,
        final_amount=amount,
        gross_return_bps=gross_return_bps,
        modeled_return_bps=modeled_return_bps,
        safety_buffer_bps=safety_buffer_bps,
        net_return_bps=net_return_bps,
        estimated_profit=start_amount * net_return_bps / BPS,
        legs=(legs[0], legs[1], legs[2]),
    )
    return SimulationOutcome(simulation)


def confirmed_capacity(
    route: TriangularRoute,
    books: Mapping[str, OrderBook],
    known_good_amount: Decimal,
    *,
    reference_prices: Mapping[str, Decimal] | None = None,
    max_doublings: int = 64,
    iterations: int = 32,
) -> Decimal:
    if not simulate_route(
        route,
        books,
        known_good_amount,
        safety_buffer_bps=ZERO,
        reference_prices=reference_prices,
    ).accepted:
        raise ValueError("known_good_amount must produce a valid route simulation")

    low = known_good_amount
    high = known_good_amount * 2
    for _ in range(max_doublings):
        if not simulate_route(
            route, books, high, safety_buffer_bps=ZERO, reference_prices=reference_prices
        ).accepted:
            break
        low = high
        high *= 2
    else:
        raise ValueError("could not find a finite confirmed-capacity upper bound")

    for _ in range(iterations):
        middle = (low + high) / 2
        if simulate_route(
            route, books, middle, safety_buffer_bps=ZERO, reference_prices=reference_prices
        ).accepted:
            low = middle
        else:
            high = middle
    return _floor(low, CAPACITY_QUANTUM)
