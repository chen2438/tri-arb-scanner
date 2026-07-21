"""Process-local scan funnel, rejection, near-miss, and rolling diagnostics."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from decimal import Decimal

from tri_arb.scanner.engine import ScannerCycle

ROLLING_WINDOW_MS = 60 * 60 * 1_000
NEAR_MISS_MIN_BPS = Decimal("0")
NEAR_MISS_LIMIT = 10


@dataclass(frozen=True, slots=True)
class NearMiss:
    exchange: str
    route_id: str
    assets: tuple[str, str, str, str]
    net_return_bps: Decimal
    estimated_profit: Decimal
    confirmed_capacity: Decimal
    market_age_ms: int
    leg_skew_ms: int


@dataclass(frozen=True, slots=True)
class ScannerDiagnostics:
    updated_at_ms: int
    total_route_count: int
    priced_route_count: int
    positive_route_count: int
    shortlisted_route_count: int
    depth_confirmed_count: int
    best_estimated_return_bps: Decimal | None
    rejection_counts: tuple[tuple[str, int], ...]
    near_misses: tuple[NearMiss, ...]
    rolling_confirmed_sample_count: int
    rolling_max_net_return_bps: Decimal | None
    rolling_buckets: tuple[tuple[str, int], ...]


class DiagnosticsTracker:
    def __init__(self) -> None:
        self._confirmed: deque[tuple[int, Decimal]] = deque()
        self._latest: ScannerDiagnostics | None = None

    def latest(self) -> ScannerDiagnostics | None:
        return self._latest

    def observe(self, cycle: ScannerCycle, *, opportunity_threshold_bps: Decimal) -> None:
        accepted = tuple(outcome for outcome in cycle.confirmations if outcome.accepted)
        for outcome in accepted:
            if outcome.simulation is not None:
                self._confirmed.append((cycle.evaluated_at_ms, outcome.simulation.net_return_bps))
        cutoff = cycle.evaluated_at_ms - ROLLING_WINDOW_MS
        while self._confirmed and self._confirmed[0][0] < cutoff:
            self._confirmed.popleft()

        near_misses = []
        for outcome in accepted:
            simulation = outcome.simulation
            capacity = outcome.confirmed_capacity_usdt
            timing = outcome.timing
            if simulation is None or capacity is None or timing is None:
                continue
            if not NEAR_MISS_MIN_BPS <= simulation.net_return_bps < opportunity_threshold_bps:
                continue
            near_misses.append(
                NearMiss(
                    exchange=simulation.route.exchange,
                    route_id=simulation.route.route_id,
                    assets=simulation.route.assets,
                    net_return_bps=simulation.net_return_bps,
                    estimated_profit=simulation.estimated_profit,
                    confirmed_capacity=capacity,
                    market_age_ms=timing.market_age_ms,
                    leg_skew_ms=timing.leg_skew_ms,
                )
            )
        near_misses.sort(key=lambda item: (-item.net_return_bps, item.route_id))

        rejection_counts = Counter(
            reason
            for outcome in cycle.confirmations
            if not outcome.accepted
            for reason in outcome.reject_reasons
        )
        values = tuple(value for _, value in self._confirmed)
        buckets = (
            ("negative", sum(value < 0 for value in values)),
            ("0_to_5", sum(0 <= value < 5 for value in values)),
            ("5_to_10", sum(5 <= value < 10 for value in values)),
            ("10_to_threshold", sum(10 <= value < opportunity_threshold_bps for value in values)),
            ("opportunity", sum(value >= opportunity_threshold_bps for value in values)),
        )
        broad = cycle.broad_screen
        self._latest = ScannerDiagnostics(
            updated_at_ms=cycle.evaluated_at_ms,
            total_route_count=(
                broad.total_route_count if broad is not None else len(cycle.broad_candidates)
            ),
            priced_route_count=(
                broad.priced_route_count if broad is not None else len(cycle.broad_candidates)
            ),
            positive_route_count=(
                broad.positive_route_count
                if broad is not None
                else sum(candidate.estimated_return_bps > 0 for candidate in cycle.broad_candidates)
            ),
            shortlisted_route_count=len(cycle.broad_candidates),
            depth_confirmed_count=len(accepted),
            best_estimated_return_bps=(
                broad.best_estimated_return_bps
                if broad is not None
                else max(
                    (candidate.estimated_return_bps for candidate in cycle.broad_candidates),
                    default=None,
                )
            ),
            rejection_counts=tuple(sorted(rejection_counts.items())),
            near_misses=tuple(near_misses[:NEAR_MISS_LIMIT]),
            rolling_confirmed_sample_count=len(values),
            rolling_max_net_return_bps=max(values, default=None),
            rolling_buckets=buckets,
        )


def diagnostics_to_public(value: ScannerDiagnostics | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "updated_at_ms": value.updated_at_ms,
        "total_route_count": value.total_route_count,
        "priced_route_count": value.priced_route_count,
        "positive_route_count": value.positive_route_count,
        "shortlisted_route_count": value.shortlisted_route_count,
        "depth_confirmed_count": value.depth_confirmed_count,
        "best_estimated_return_bps": (
            str(value.best_estimated_return_bps)
            if value.best_estimated_return_bps is not None
            else None
        ),
        "rejection_counts": dict(value.rejection_counts),
        "near_misses": [
            {
                "exchange": item.exchange,
                "route_id": item.route_id,
                "assets": list(item.assets),
                "net_return_bps": str(item.net_return_bps),
                "estimated_profit": str(item.estimated_profit),
                "confirmed_capacity": str(item.confirmed_capacity),
                "market_age_ms": item.market_age_ms,
                "leg_skew_ms": item.leg_skew_ms,
            }
            for item in value.near_misses
        ],
        "rolling_confirmed_sample_count": value.rolling_confirmed_sample_count,
        "rolling_max_net_return_bps": (
            str(value.rolling_max_net_return_bps)
            if value.rolling_max_net_return_bps is not None
            else None
        ),
        "rolling_buckets": dict(value.rolling_buckets),
    }
