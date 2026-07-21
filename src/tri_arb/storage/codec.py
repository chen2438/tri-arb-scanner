"""Stable JSON audit codec with enough inputs to replay a confirmed route."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from tri_arb.domain.models import (
    BookLevel,
    ConversionEdge,
    ConversionSide,
    MarketRules,
    OrderBook,
    RouteSimulation,
    SimulationOutcome,
    TriangularRoute,
)
from tri_arb.domain.simulation import simulate_route
from tri_arb.scanner.lifecycle import OpportunityLifecycle

AUDIT_SCHEMA_VERSION = 1


def _market(market: MarketRules) -> dict[str, Any]:
    return {
        "symbol": market.symbol,
        "base_asset": market.base_asset,
        "quote_asset": market.quote_asset,
        "base_asset_precision": market.base_asset_precision,
        "min_base_quantity": str(market.min_base_quantity),
        "min_quote_amount": str(market.min_quote_amount),
        "max_quote_amount": str(market.max_quote_amount),
        "taker_commission": str(market.taker_commission),
        "allowed_sides": sorted(side.value for side in market.allowed_sides),
    }


def _route(route: TriangularRoute) -> dict[str, Any]:
    return {
        "route_id": route.route_id,
        "assets": list(route.assets),
        "edges": [
            {
                "market": _market(edge.market),
                "from_asset": edge.from_asset,
                "to_asset": edge.to_asset,
                "side": edge.side.value,
            }
            for edge in route.edges
        ],
    }


def _book(book: OrderBook) -> dict[str, Any]:
    def levels(values: tuple[BookLevel, ...]) -> list[list[str]]:
        return [[str(level.price), str(level.quantity)] for level in values]

    return {
        "symbol": book.symbol,
        "bids": levels(book.bids),
        "asks": levels(book.asks),
        "version": book.version,
        "source_time_ms": book.source_time_ms,
        "received_time_ms": book.received_time_ms,
    }


def _simulation(simulation: RouteSimulation) -> dict[str, Any]:
    return {
        "start_amount": str(simulation.start_amount),
        "final_amount": str(simulation.final_amount),
        "gross_return_bps": str(simulation.gross_return_bps),
        "modeled_return_bps": str(simulation.modeled_return_bps),
        "safety_buffer_bps": str(simulation.safety_buffer_bps),
        "net_return_bps": str(simulation.net_return_bps),
        "estimated_profit": str(simulation.estimated_profit),
        "legs": [
            {
                "symbol": leg.symbol,
                "side": leg.side.value,
                "from_asset": leg.from_asset,
                "to_asset": leg.to_asset,
                "input_amount": str(leg.input_amount),
                "output_amount": str(leg.output_amount),
                "average_price": str(leg.average_price),
                "fee_rate": str(leg.fee_rate),
                "fee_amount": str(leg.fee_amount),
                "dust_amount": str(leg.dust_amount),
                "levels_consumed": leg.levels_consumed,
                "book_version": leg.book_version,
                "source_time_ms": leg.source_time_ms,
                "received_time_ms": leg.received_time_ms,
            }
            for leg in simulation.legs
        ],
    }


def serialize_lifecycle(lifecycle: OpportunityLifecycle) -> str:
    confirmation = lifecycle.current_confirmation
    simulation = confirmation.simulation
    if simulation is None or confirmation.timing is None:
        raise ValueError("lifecycle audit snapshot requires an accepted confirmation")
    route_symbols = {edge.market.symbol for edge in confirmation.candidate.route.edges}
    book_symbols = {book.symbol for book in confirmation.books}
    if len(confirmation.books) != 3 or book_symbols != route_symbols:
        raise ValueError("lifecycle audit snapshot requires all three route books")
    payload = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "lifecycle": {
            "lifecycle_id": lifecycle.lifecycle_id,
            "route_id": lifecycle.route_id,
            "assets": list(lifecycle.assets),
            "first_seen_ms": lifecycle.first_seen_ms,
            "last_confirmed_ms": lifecycle.last_confirmed_ms,
            "peak_net_return_bps": str(lifecycle.peak_net_return_bps),
            "confirmed_capacity_usdt": str(lifecycle.confirmed_capacity_usdt),
            "closed_at_ms": lifecycle.closed_at_ms,
            "close_reason": lifecycle.close_reason.value if lifecycle.close_reason else None,
        },
        "confirmation": {
            "route": _route(confirmation.candidate.route),
            "books": [_book(book) for book in confirmation.books],
            "simulation": _simulation(simulation),
            "timing": {
                "market_age_ms": confirmation.timing.market_age_ms,
                "leg_skew_ms": confirmation.timing.leg_skew_ms,
            },
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _decode_market(payload: dict[str, Any]) -> MarketRules:
    return MarketRules(
        symbol=payload["symbol"],
        base_asset=payload["base_asset"],
        quote_asset=payload["quote_asset"],
        base_asset_precision=payload["base_asset_precision"],
        min_base_quantity=Decimal(payload["min_base_quantity"]),
        min_quote_amount=Decimal(payload["min_quote_amount"]),
        max_quote_amount=Decimal(payload["max_quote_amount"]),
        taker_commission=Decimal(payload["taker_commission"]),
        allowed_sides=frozenset(ConversionSide(side) for side in payload["allowed_sides"]),
    )


def _decode_route(payload: dict[str, Any]) -> TriangularRoute:
    edges = tuple(
        ConversionEdge(
            market=_decode_market(edge["market"]),
            from_asset=edge["from_asset"],
            to_asset=edge["to_asset"],
            side=ConversionSide(edge["side"]),
        )
        for edge in payload["edges"]
    )
    if len(edges) != 3:
        raise ValueError("audit route must contain three edges")
    assets = tuple(payload["assets"])
    if len(assets) != 4:
        raise ValueError("audit route must contain four assets")
    return TriangularRoute(
        route_id=payload["route_id"],
        assets=assets,  # type: ignore[arg-type]
        edges=edges,  # type: ignore[arg-type]
    )


def _decode_book(payload: dict[str, Any]) -> OrderBook:
    def levels(values: list[list[str]]) -> tuple[BookLevel, ...]:
        return tuple(BookLevel(Decimal(price), Decimal(quantity)) for price, quantity in values)

    return OrderBook(
        symbol=payload["symbol"],
        bids=levels(payload["bids"]),
        asks=levels(payload["asks"]),
        version=payload["version"],
        source_time_ms=payload["source_time_ms"],
        received_time_ms=payload["received_time_ms"],
    )


@dataclass(frozen=True, slots=True)
class ReplayResult:
    outcome: SimulationOutcome
    matches_recorded: bool


def replay_audit_snapshot(snapshot_json: str) -> ReplayResult:
    try:
        payload = json.loads(snapshot_json)
        if payload["schema_version"] != AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported audit schema version")
        confirmation = payload["confirmation"]
        route = _decode_route(confirmation["route"])
        books = tuple(_decode_book(book) for book in confirmation["books"])
        if len(books) != 3 or len({book.symbol for book in books}) != 3:
            raise ValueError("audit snapshot must contain three distinct books")
        recorded = confirmation["simulation"]
        outcome = simulate_route(
            route,
            {book.symbol: book for book in books},
            Decimal(recorded["start_amount"]),
            safety_buffer_bps=Decimal(recorded["safety_buffer_bps"]),
        )
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid audit snapshot") from error
    simulation = outcome.simulation
    matches = simulation is not None and all(
        (
            str(simulation.final_amount) == recorded["final_amount"],
            str(simulation.gross_return_bps) == recorded["gross_return_bps"],
            str(simulation.modeled_return_bps) == recorded["modeled_return_bps"],
            str(simulation.net_return_bps) == recorded["net_return_bps"],
            str(simulation.estimated_profit) == recorded["estimated_profit"],
        )
    )
    return ReplayResult(outcome=outcome, matches_recorded=matches)
