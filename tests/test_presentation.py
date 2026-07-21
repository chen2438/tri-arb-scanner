from decimal import Decimal

from tests.test_storage import _confirmed, _cycle
from tri_arb.presentation import audit_snapshot_to_public, opportunity_to_public
from tri_arb.scanner import OpportunityTracker
from tri_arb.storage import serialize_lifecycle


def test_current_and_stored_opportunity_use_identical_decimal_string_contract() -> None:
    tracker = OpportunityTracker(lifecycle_id=lambda: "00000000-0000-0000-0000-000000000001")
    tracker.apply_cycle(_cycle(1_000, _confirmed("510")))
    (lifecycle,) = tracker.active()

    current = opportunity_to_public(lifecycle)
    stored = audit_snapshot_to_public(serialize_lifecycle(lifecycle))

    assert current == stored
    assert current["state"] == "active"
    assert isinstance(current["net_return_bps"], str)
    assert Decimal(current["net_return_bps"]) == Decimal("195")
    assert Decimal(current["estimated_profit_usdt"]) == Decimal("1.95")
    assert current["first_seen_at"] == "1970-01-01T00:00:01.000Z"
    assert current["depth_confirmed"] is True
    assert current["anchor_asset"] == "USDT"
    assert current["estimated_profit"] == current["estimated_profit_usdt"]
    assert current["confirmed_capacity"] == current["confirmed_capacity_usdt"]
    assert len(current["legs"]) == 3
    assert current["legs"][0]["source_time"] == "1970-01-01T00:16:40.000Z"


def test_closed_opportunity_exposes_reason_and_utc_close_time() -> None:
    tracker = OpportunityTracker(lifecycle_id=lambda: "00000000-0000-0000-0000-000000000002")
    accepted = _confirmed("510")
    tracker.apply_cycle(_cycle(1_000, accepted))
    (event,) = tracker.close_for_restart(2_000)

    public = opportunity_to_public(event.lifecycle)

    assert public["state"] == "closed"
    assert public["closed_at"] == "1970-01-01T00:00:02.000Z"
    assert public["close_reason"] == "process_restart"
