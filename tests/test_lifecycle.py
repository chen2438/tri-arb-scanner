from dataclasses import replace
from decimal import Decimal

from tests.test_confirmation import _inputs
from tests.test_subscriptions import _route
from tri_arb.scanner import (
    BroadCandidate,
    CloseReason,
    ConfirmationOutcome,
    LifecycleEventType,
    OpportunityTracker,
    ScannerCycle,
    confirm_candidate,
)


def _accepted(net_return_bps: str = "20") -> ConfirmationOutcome:
    candidate, updates, plan, statuses = _inputs()
    outcome = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        safety_buffer_bps=Decimal("5"),
    )
    assert outcome.simulation is not None
    simulation = replace(
        outcome.simulation,
        net_return_bps=Decimal(net_return_bps),
        estimated_profit=outcome.simulation.start_amount
        * Decimal(net_return_bps)
        / Decimal("10000"),
    )
    return replace(outcome, simulation=simulation)


def _cycle(at_ms: int, *outcomes: ConfirmationOutcome) -> ScannerCycle:
    return ScannerCycle(
        evaluated_at_ms=at_ms,
        broad_candidates=tuple(outcome.candidate for outcome in outcomes),
        confirmations=outcomes,
    )


def test_opens_at_exact_threshold_and_closes_after_two_values_strictly_below_close() -> None:
    tracker = OpportunityTracker(lifecycle_id=lambda: "lifecycle-1")

    opened = tracker.apply_cycle(_cycle(1_000, _accepted("20")))
    equal_close = tracker.apply_cycle(_cycle(2_000, _accepted("15")))
    first_below = tracker.apply_cycle(_cycle(3_000, _accepted("14.99")))
    second_below = tracker.apply_cycle(_cycle(4_000, _accepted("14")))

    assert opened[0].event_type is LifecycleEventType.OPENED
    assert opened[0].lifecycle.lifecycle_id == "lifecycle-1"
    assert equal_close == ()
    assert first_below == ()
    assert second_below[0].event_type is LifecycleEventType.CLOSED
    assert second_below[0].lifecycle.close_reason is CloseReason.BELOW_THRESHOLD
    assert tracker.active() == ()


def test_invalid_confirmation_or_missing_route_closes_immediately() -> None:
    ids = iter(["one", "two"])
    tracker = OpportunityTracker(lifecycle_id=lambda: next(ids))
    accepted = _accepted("25")
    tracker.apply_cycle(_cycle(1_000, accepted))
    invalid = replace(
        accepted,
        simulation=None,
        confirmed_capacity_usdt=None,
        timing=None,
        reject_reasons=("stale_depth",),
    )

    closed = tracker.apply_cycle(_cycle(2_000, invalid))

    assert closed[0].lifecycle.close_reason is CloseReason.STALE_DEPTH
    tracker.apply_cycle(_cycle(3_000, accepted))
    unavailable = tracker.apply_cycle(_cycle(4_000))
    assert unavailable[0].lifecycle.close_reason is CloseReason.ROUTE_UNAVAILABLE


def test_emits_peak_only_after_one_full_basis_point_and_closes_on_restart() -> None:
    tracker = OpportunityTracker(lifecycle_id=lambda: "one")
    tracker.apply_cycle(_cycle(1_000, _accepted("20")))

    assert tracker.apply_cycle(_cycle(2_000, _accepted("20.99"))) == ()
    peak = tracker.apply_cycle(_cycle(3_000, _accepted("21")))
    restarted = tracker.close_for_restart(4_000)

    assert peak[0].event_type is LifecycleEventType.PEAK
    assert peak[0].lifecycle.peak_net_return_bps == Decimal("21")
    assert restarted[0].lifecycle.close_reason is CloseReason.PROCESS_RESTART


def test_active_opportunities_have_stable_profit_ordering() -> None:
    ids = iter(["one", "two"])
    tracker = OpportunityTracker(lifecycle_id=lambda: next(ids))
    first = _accepted("21")
    second_route = _route(9)
    assert first.simulation is not None
    second = replace(
        first,
        candidate=BroadCandidate(second_route, Decimal("100"), Decimal("101"), Decimal("30")),
        simulation=replace(first.simulation, route=second_route, net_return_bps=Decimal("30")),
    )

    tracker.apply_cycle(_cycle(1_000, first, second))

    assert tuple(item.lifecycle_id for item in tracker.active()) == ("two", "one")
