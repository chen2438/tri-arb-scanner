"""Fail-closed confirmation of shortlisted routes against current depth generations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from tri_arb.domain.models import OrderBook, PriceLimit, PriceReference, RouteSimulation
from tri_arb.domain.simulation import confirmed_capacity, simulate_route
from tri_arb.exchange.mexc import (
    DepthTiming,
    DepthTimingError,
    DepthUpdate,
    SubscriptionPlan,
    WebSocketStatus,
    validate_depth_timing,
)
from tri_arb.scanner.screening import BroadCandidate


class ConfirmationRejectReason(StrEnum):
    CLOCK_UNAVAILABLE = "clock_unavailable"
    NOT_SELECTED = "not_selected"
    MISSING_CURRENT_DEPTH = "missing_current_depth"
    WRONG_SHARD = "wrong_shard"
    STALE_GENERATION = "stale_generation"
    CAPACITY_UNAVAILABLE = "capacity_unavailable"
    MISSING_PRICE_REFERENCE = "missing_price_reference"
    STALE_PRICE_REFERENCE = "stale_price_reference"
    MISSING_PRICE_LIMIT = "missing_price_limit"
    STALE_PRICE_LIMIT = "stale_price_limit"


@dataclass(frozen=True, slots=True)
class ConfirmationOutcome:
    candidate: BroadCandidate
    simulation: RouteSimulation | None
    confirmed_capacity_usdt: Decimal | None
    timing: DepthTiming | None
    reject_reasons: tuple[str, ...]
    books: tuple[OrderBook, ...] = ()
    price_limits: tuple[PriceLimit, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.simulation is not None and self.confirmed_capacity_usdt is not None


def confirm_candidate(
    candidate: BroadCandidate,
    depth_updates: Mapping[str, DepthUpdate],
    plan: SubscriptionPlan,
    websocket_statuses: tuple[WebSocketStatus, ...],
    *,
    server_time_ms: int,
    safety_buffer_bps: Decimal,
    price_references: Mapping[str, PriceReference] | None = None,
    price_limits: Mapping[str, PriceLimit] | None = None,
    local_time_ms: int | None = None,
    max_price_reference_age_ms: int = 30_000,
    max_age_ms: int = 2_000,
    max_leg_skew_ms: int = 1_000,
) -> ConfirmationOutcome:
    if candidate.route.route_id not in plan.selected_route_ids:
        return ConfirmationOutcome(
            candidate, None, None, None, (ConfirmationRejectReason.NOT_SELECTED.value,)
        )
    shard_by_symbol = {
        symbol: shard_id for shard_id, symbols in enumerate(plan.shards) for symbol in symbols
    }
    generation_by_shard = {
        status.shard_id: status.connection_generation for status in websocket_statuses
    }
    updates: list[DepthUpdate] = []
    reasons: list[str] = []
    for edge in candidate.route.edges:
        update = depth_updates.get(edge.market.symbol)
        if update is None:
            reasons.append(ConfirmationRejectReason.MISSING_CURRENT_DEPTH.value)
            continue
        expected_shard = shard_by_symbol.get(edge.market.symbol)
        if expected_shard != update.shard_id:
            reasons.append(ConfirmationRejectReason.WRONG_SHARD.value)
            continue
        if generation_by_shard.get(update.shard_id) != update.connection_generation:
            reasons.append(ConfirmationRejectReason.STALE_GENERATION.value)
            continue
        updates.append(update)
    if reasons:
        return ConfirmationOutcome(candidate, None, None, None, tuple(dict.fromkeys(reasons)))

    reference_prices: dict[str, Decimal] = {}
    current_price_limits: dict[str, PriceLimit] = {}
    received_now_ms = server_time_ms if local_time_ms is None else local_time_ms
    for edge in candidate.route.edges:
        if edge.market.price_protection is None:
            continue
        reference = (price_references or {}).get(edge.market.symbol)
        if reference is None or reference.symbol != edge.market.symbol:
            reasons.append(ConfirmationRejectReason.MISSING_PRICE_REFERENCE.value)
            continue
        age_ms = received_now_ms - reference.received_time_ms
        source_age_ms = (
            abs(server_time_ms - reference.source_time_ms)
            if reference.source_time_ms is not None
            else 0
        )
        if (
            age_ms < 0
            or age_ms > max_price_reference_age_ms
            or source_age_ms > max_price_reference_age_ms
        ):
            reasons.append(ConfirmationRejectReason.STALE_PRICE_REFERENCE.value)
            continue
        reference_prices[edge.market.symbol] = reference.price
    for edge in candidate.route.edges:
        if not edge.market.requires_explicit_price_limit:
            continue
        price_limit = (price_limits or {}).get(edge.market.symbol)
        if price_limit is None or price_limit.symbol != edge.market.symbol:
            reasons.append(ConfirmationRejectReason.MISSING_PRICE_LIMIT.value)
            continue
        age_ms = received_now_ms - price_limit.received_time_ms
        source_age_ms = abs(server_time_ms - price_limit.source_time_ms)
        if (
            age_ms < 0
            or age_ms > max_price_reference_age_ms
            or source_age_ms > max_price_reference_age_ms
        ):
            reasons.append(ConfirmationRejectReason.STALE_PRICE_LIMIT.value)
            continue
        current_price_limits[edge.market.symbol] = price_limit
    if reasons:
        return ConfirmationOutcome(candidate, None, None, None, tuple(dict.fromkeys(reasons)))

    books = tuple(update.book for update in updates)
    try:
        timing = validate_depth_timing(
            books,
            server_time_ms=server_time_ms,
            max_age_ms=max_age_ms,
            max_leg_skew_ms=max_leg_skew_ms,
        )
    except DepthTimingError as error:
        return ConfirmationOutcome(candidate, None, None, None, (error.violation.value,), books)
    books_by_symbol = {book.symbol: book for book in books}
    outcome = simulate_route(
        candidate.route,
        books_by_symbol,
        candidate.start_amount,
        safety_buffer_bps=safety_buffer_bps,
        reference_prices=reference_prices,
        price_limits=current_price_limits,
    )
    if outcome.simulation is None:
        return ConfirmationOutcome(
            candidate,
            None,
            None,
            timing,
            tuple(reason.value for reason in outcome.reject_reasons),
            books,
            tuple(current_price_limits.values()),
        )
    try:
        capacity = confirmed_capacity(
            candidate.route,
            books_by_symbol,
            candidate.start_amount,
            reference_prices=reference_prices,
            price_limits=current_price_limits,
        )
    except ValueError:
        return ConfirmationOutcome(
            candidate,
            None,
            None,
            timing,
            (ConfirmationRejectReason.CAPACITY_UNAVAILABLE.value,),
            books,
            tuple(current_price_limits.values()),
        )
    return ConfirmationOutcome(
        candidate,
        outcome.simulation,
        capacity,
        timing,
        (),
        books,
        tuple(current_price_limits.values()),
    )
