"""Deterministic opportunity lifecycle state machine."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from tri_arb.domain.models import RouteSimulation
from tri_arb.exchange.mexc import DepthTiming
from tri_arb.scanner.confirmation import ConfirmationOutcome
from tri_arb.scanner.engine import ScannerCycle


class LifecycleEventType(StrEnum):
    OPENED = "opened"
    PEAK = "peak"
    CLOSED = "closed"


class CloseReason(StrEnum):
    BELOW_THRESHOLD = "below_threshold"
    ROUTE_UNAVAILABLE = "route_unavailable"
    SUBSCRIPTION_REMOVED = "subscription_removed"
    MISSING_DEPTH = "missing_depth"
    CONNECTION_LOST = "connection_lost"
    STALE_DEPTH = "stale_depth"
    LEG_SKEW = "leg_skew"
    SIMULATION_REJECTED = "simulation_rejected"
    PROCESS_RESTART = "process_restart"


@dataclass(frozen=True, slots=True)
class OpportunityLifecycle:
    lifecycle_id: str
    route_id: str
    assets: tuple[str, str, str, str]
    first_seen_ms: int
    last_confirmed_ms: int
    peak_net_return_bps: Decimal
    last_event_peak_bps: Decimal
    current_simulation: RouteSimulation
    confirmed_capacity_usdt: Decimal
    timing: DepthTiming
    consecutive_below_close: int = 0
    closed_at_ms: int | None = None
    close_reason: CloseReason | None = None

    @property
    def active(self) -> bool:
        return self.closed_at_ms is None


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    event_type: LifecycleEventType
    occurred_at_ms: int
    lifecycle: OpportunityLifecycle


def _default_id() -> str:
    return str(uuid.uuid4())


def _invalid_close_reason(outcome: ConfirmationOutcome) -> CloseReason:
    reasons = set(outcome.reject_reasons)
    if "not_selected" in reasons:
        return CloseReason.SUBSCRIPTION_REMOVED
    if "missing_current_depth" in reasons:
        return CloseReason.MISSING_DEPTH
    if reasons & {"wrong_shard", "stale_generation"}:
        return CloseReason.CONNECTION_LOST
    if "stale_depth" in reasons:
        return CloseReason.STALE_DEPTH
    if "leg_skew" in reasons:
        return CloseReason.LEG_SKEW
    return CloseReason.SIMULATION_REJECTED


class OpportunityTracker:
    def __init__(
        self,
        *,
        open_threshold_bps: Decimal = Decimal("20"),
        close_threshold_bps: Decimal = Decimal("15"),
        lifecycle_id: Callable[[], str] = _default_id,
    ) -> None:
        if not open_threshold_bps.is_finite() or not close_threshold_bps.is_finite():
            raise ValueError("opportunity thresholds must be finite")
        if close_threshold_bps < 0 or close_threshold_bps >= open_threshold_bps:
            raise ValueError("close threshold must be lower than open threshold")
        self._open_threshold = open_threshold_bps
        self._close_threshold = close_threshold_bps
        self._lifecycle_id = lifecycle_id
        self._active: dict[str, OpportunityLifecycle] = {}
        self._seen_ids: set[str] = set()
        self._last_cycle_ms = 0

    def active(self) -> tuple[OpportunityLifecycle, ...]:
        return tuple(
            sorted(
                self._active.values(),
                key=lambda lifecycle: (
                    -lifecycle.current_simulation.net_return_bps,
                    -lifecycle.last_confirmed_ms,
                    lifecycle.route_id,
                ),
            )
        )

    def _close(
        self,
        route_id: str,
        reason: CloseReason,
        occurred_at_ms: int,
    ) -> LifecycleEvent:
        current = self._active.pop(route_id)
        closed = replace(current, closed_at_ms=occurred_at_ms, close_reason=reason)
        return LifecycleEvent(LifecycleEventType.CLOSED, occurred_at_ms, closed)

    def _open(self, outcome: ConfirmationOutcome, occurred_at_ms: int) -> LifecycleEvent:
        simulation = outcome.simulation
        capacity = outcome.confirmed_capacity_usdt
        timing = outcome.timing
        if simulation is None or capacity is None or timing is None:
            raise ValueError("cannot open an unconfirmed opportunity")
        if simulation.route.route_id != outcome.candidate.route.route_id:
            raise ValueError("confirmation route identity mismatch")
        lifecycle_id = self._lifecycle_id()
        if not lifecycle_id or lifecycle_id in self._seen_ids:
            raise ValueError("lifecycle IDs must be non-empty and globally unique")
        self._seen_ids.add(lifecycle_id)
        lifecycle = OpportunityLifecycle(
            lifecycle_id=lifecycle_id,
            route_id=outcome.candidate.route.route_id,
            assets=outcome.candidate.route.assets,
            first_seen_ms=occurred_at_ms,
            last_confirmed_ms=occurred_at_ms,
            peak_net_return_bps=simulation.net_return_bps,
            last_event_peak_bps=simulation.net_return_bps,
            current_simulation=simulation,
            confirmed_capacity_usdt=capacity,
            timing=timing,
        )
        self._active[lifecycle.route_id] = lifecycle
        return LifecycleEvent(LifecycleEventType.OPENED, occurred_at_ms, lifecycle)

    def apply_cycle(self, cycle: ScannerCycle) -> tuple[LifecycleEvent, ...]:
        if cycle.evaluated_at_ms <= 0 or cycle.evaluated_at_ms < self._last_cycle_ms:
            raise ValueError("scanner cycle time must be positive and monotonic")
        self._last_cycle_ms = cycle.evaluated_at_ms
        outcomes: dict[str, ConfirmationOutcome] = {}
        for outcome in cycle.confirmations:
            route_id = outcome.candidate.route.route_id
            if route_id in outcomes:
                raise ValueError(f"duplicate confirmation outcome: {route_id}")
            outcomes[route_id] = outcome

        events: list[LifecycleEvent] = []
        for route_id in tuple(self._active):
            if route_id not in outcomes:
                events.append(
                    self._close(route_id, CloseReason.ROUTE_UNAVAILABLE, cycle.evaluated_at_ms)
                )

        for route_id, outcome in outcomes.items():
            current = self._active.get(route_id)
            if not outcome.accepted:
                if current is not None:
                    events.append(
                        self._close(
                            route_id,
                            _invalid_close_reason(outcome),
                            cycle.evaluated_at_ms,
                        )
                    )
                continue
            simulation = outcome.simulation
            capacity = outcome.confirmed_capacity_usdt
            timing = outcome.timing
            if simulation is None or capacity is None or timing is None:
                raise AssertionError("accepted confirmation is incomplete")
            if simulation.route.route_id != route_id:
                raise ValueError("confirmation route identity mismatch")
            if current is None:
                if simulation.net_return_bps >= self._open_threshold:
                    events.append(self._open(outcome, cycle.evaluated_at_ms))
                continue

            below_count = (
                current.consecutive_below_close + 1
                if simulation.net_return_bps < self._close_threshold
                else 0
            )
            peak = max(current.peak_net_return_bps, simulation.net_return_bps)
            updated = replace(
                current,
                last_confirmed_ms=cycle.evaluated_at_ms,
                peak_net_return_bps=peak,
                current_simulation=simulation,
                confirmed_capacity_usdt=capacity,
                timing=timing,
                consecutive_below_close=below_count,
            )
            self._active[route_id] = updated
            if peak >= current.last_event_peak_bps + Decimal("1"):
                updated = replace(updated, last_event_peak_bps=peak)
                self._active[route_id] = updated
                events.append(
                    LifecycleEvent(LifecycleEventType.PEAK, cycle.evaluated_at_ms, updated)
                )
            if below_count >= 2:
                events.append(
                    self._close(
                        route_id,
                        CloseReason.BELOW_THRESHOLD,
                        cycle.evaluated_at_ms,
                    )
                )
        return tuple(events)

    def close_for_restart(self, occurred_at_ms: int) -> tuple[LifecycleEvent, ...]:
        if occurred_at_ms <= 0 or occurred_at_ms < self._last_cycle_ms:
            raise ValueError("restart close time must be positive and monotonic")
        self._last_cycle_ms = occurred_at_ms
        return tuple(
            self._close(route_id, CloseReason.PROCESS_RESTART, occurred_at_ms)
            for route_id in sorted(self._active)
        )
