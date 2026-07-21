"""Stable JSON-ready public representations for opportunities and audit rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from tri_arb.scanner.lifecycle import OpportunityLifecycle
from tri_arb.storage.codec import AUDIT_SCHEMA_VERSION, serialize_lifecycle


def utc_iso(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    if timestamp_ms <= 0:
        raise ValueError("timestamp must be positive")
    value = datetime.fromtimestamp(timestamp_ms / 1_000, tz=UTC).isoformat(timespec="milliseconds")
    return value.replace("+00:00", "Z")


def audit_snapshot_to_public(snapshot_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(snapshot_json)
        if payload["schema_version"] != AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported audit schema version")
        lifecycle = payload["lifecycle"]
        confirmation = payload["confirmation"]
        simulation = confirmation["simulation"]
        timing = confirmation["timing"]
        route = confirmation["route"]
        legs = [
            {
                "symbol": leg["symbol"],
                "side": leg["side"],
                "from_asset": leg["from_asset"],
                "to_asset": leg["to_asset"],
                "input_amount": leg["input_amount"],
                "output_amount": leg["output_amount"],
                "average_price": leg["average_price"],
                "fee_rate": leg["fee_rate"],
                "fee_amount": leg["fee_amount"],
                "dust_amount": leg["dust_amount"],
                "levels_consumed": leg["levels_consumed"],
                "book_version": leg["book_version"],
                "source_time": utc_iso(leg["source_time_ms"]),
                "received_time": utc_iso(leg["received_time_ms"]),
                "price_reference": leg.get("price_reference"),
                "price_protection_limit": leg.get("price_protection_limit"),
            }
            for leg in simulation["legs"]
        ]
        closed_at = lifecycle["closed_at_ms"]
        return {
            "id": lifecycle["lifecycle_id"],
            "route_id": lifecycle["route_id"],
            "state": "closed" if closed_at is not None else "active",
            "assets": route["assets"],
            "anchor_asset": route["assets"][0],
            "start_amount": simulation["start_amount"],
            "final_amount": simulation["final_amount"],
            "gross_return_bps": simulation["gross_return_bps"],
            "modeled_return_bps": simulation["modeled_return_bps"],
            "safety_buffer_bps": simulation["safety_buffer_bps"],
            "net_return_bps": simulation["net_return_bps"],
            "estimated_profit_usdt": simulation["estimated_profit"],
            "estimated_profit": simulation["estimated_profit"],
            "confirmed_capacity_usdt": lifecycle["confirmed_capacity_usdt"],
            "confirmed_capacity": lifecycle["confirmed_capacity_usdt"],
            "first_seen_at": utc_iso(lifecycle["first_seen_ms"]),
            "last_confirmed_at": utc_iso(lifecycle["last_confirmed_ms"]),
            "closed_at": utc_iso(closed_at),
            "peak_net_return_bps": lifecycle["peak_net_return_bps"],
            "close_reason": lifecycle["close_reason"],
            "market_age_ms": timing["market_age_ms"],
            "leg_skew_ms": timing["leg_skew_ms"],
            "depth_confirmed": True,
            "execution_warning": "预估结果，三腿无法原子成交，不保证实际利润",  # noqa: RUF001
            "legs": legs,
        }
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid opportunity audit snapshot") from error


def opportunity_to_public(lifecycle: OpportunityLifecycle) -> dict[str, Any]:
    return audit_snapshot_to_public(serialize_lifecycle(lifecycle))
