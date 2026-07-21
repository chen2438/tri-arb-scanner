import asyncio
import json
from dataclasses import replace
from decimal import Decimal

import aiosqlite
import pytest

from tests.test_confirmation import _inputs
from tri_arb.domain.models import BookLevel, PriceProtection, PriceReference
from tri_arb.scanner import OpportunityTracker, ScannerCycle, confirm_candidate
from tri_arb.storage import OpportunityStore, replay_audit_snapshot, serialize_lifecycle


def _confirmed(third_leg_bid: str):
    candidate, updates, plan, statuses = _inputs()
    third_symbol = candidate.route.edges[2].market.symbol
    update = updates[third_symbol]
    book = replace(
        update.book,
        bids=(BookLevel(Decimal(third_leg_bid), Decimal("1000000")),),
    )
    updates[third_symbol] = replace(update, book=book)
    outcome = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        safety_buffer_bps=Decimal("5"),
    )
    assert outcome.accepted
    return outcome


def _cycle(at_ms: int, *outcomes) -> ScannerCycle:
    return ScannerCycle(at_ms, tuple(outcome.candidate for outcome in outcomes), outcomes)


def test_replays_price_protection_rules_and_reference_prices() -> None:
    candidate, updates, plan, statuses = _inputs()
    first = candidate.route.edges[0]
    protected = replace(
        first,
        market=replace(
            first.market,
            price_protection=PriceProtection(Decimal("0.2"), Decimal("0.2")),
        ),
    )
    candidate = replace(
        candidate,
        route=replace(candidate.route, edges=(protected, *candidate.route.edges[1:])),
    )
    outcome = confirm_candidate(
        candidate,
        updates,
        plan,
        statuses,
        server_time_ms=1_000_100,
        local_time_ms=1_000_100,
        price_references={
            protected.market.symbol: PriceReference(
                protected.market.symbol, Decimal("9000"), 5, 1_000_050
            )
        },
        safety_buffer_bps=Decimal("5"),
    )
    assert outcome.accepted
    tracker = OpportunityTracker(lifecycle_id=lambda: "protected-lifecycle")
    tracker.apply_cycle(_cycle(1_000, outcome))
    snapshot = serialize_lifecycle(tracker.active()[0])

    assert '"price_reference":"9000"' in snapshot
    assert replay_audit_snapshot(snapshot).matches_recorded


@pytest.mark.asyncio
async def test_persists_open_peak_close_events_and_replays_every_snapshot(tmp_path) -> None:
    database_path = tmp_path / "audit.db"
    store = OpportunityStore(f"sqlite+aiosqlite:///{database_path}")
    assert await store.start(started_at_ms=500) == 0
    tracker = OpportunityTracker(lifecycle_id=lambda: "00000000-0000-0000-0000-000000000001")

    opened = tracker.apply_cycle(_cycle(1_000, _confirmed("510")))
    peaked = tracker.apply_cycle(_cycle(2_000, _confirmed("510.05")))
    valid = _confirmed("510.05")
    invalid = replace(
        valid,
        simulation=None,
        confirmed_capacity_usdt=None,
        timing=None,
        reject_reasons=("stale_depth",),
    )
    closed = tracker.apply_cycle(_cycle(3_000, invalid))
    await asyncio.gather(store.record_events(opened), store.record_events(opened))
    await store.record_events(peaked)
    await store.record_events(closed)

    stored_lifecycles = await store.list_lifecycles()
    stored_events = await store.list_events()
    async with aiosqlite.connect(database_path) as connection:
        journal_mode = (await (await connection.execute("PRAGMA journal_mode")).fetchone())[0]
    await store.stop()

    assert journal_mode == "wal"
    assert len(stored_lifecycles) == 1
    assert stored_lifecycles[0].state == "closed"
    assert stored_lifecycles[0].close_reason == "stale_depth"
    assert tuple(event.event_type for event in stored_events) == ("opened", "peak", "closed")
    assert all(
        replay_audit_snapshot(event.snapshot_json).matches_recorded for event in stored_events
    )
    tampered = json.loads(stored_events[0].snapshot_json)
    tampered["confirmation"]["simulation"]["final_amount"] = "999"
    assert not replay_audit_snapshot(json.dumps(tampered)).matches_recorded


@pytest.mark.asyncio
async def test_startup_closes_residual_active_rows_and_retention_cascades_events(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    first_store = OpportunityStore(database_url)
    await first_store.start(started_at_ms=500)
    tracker = OpportunityTracker(lifecycle_id=lambda: "00000000-0000-0000-0000-000000000002")
    await first_store.record_events(tracker.apply_cycle(_cycle(1_000, _confirmed("510"))))
    assert await first_store.cleanup(10_000) == 0
    await first_store.stop()

    second_store = OpportunityStore(database_url)
    assert await second_store.start(started_at_ms=2_000) == 1
    (lifecycle,) = await second_store.list_lifecycles()
    events = await second_store.list_events()

    assert lifecycle.state == "closed"
    assert lifecycle.close_reason == "process_restart"
    assert tuple(event.event_type for event in events) == ("opened", "closed")
    assert await second_store.cleanup(2_001) == 1
    assert await second_store.list_lifecycles() == ()
    assert await second_store.list_events() == ()
    await second_store.stop()


@pytest.mark.asyncio
async def test_rejects_unknown_schema_version_without_starting_writer(tmp_path) -> None:
    database_path = tmp_path / "version.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    first = OpportunityStore(database_url)
    await first.start(started_at_ms=1)
    await first.stop()
    async with aiosqlite.connect(database_path) as connection:
        await connection.execute("UPDATE schema_version SET version = 99 WHERE id = 1")
        await connection.commit()

    incompatible = OpportunityStore(database_url)
    with pytest.raises(RuntimeError, match="schema version 99"):
        await incompatible.start(started_at_ms=2)
