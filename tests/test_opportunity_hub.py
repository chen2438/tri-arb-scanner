import pytest

from tests.test_storage import _confirmed, _cycle
from tri_arb.scanner import OpportunityTracker
from tri_arb.services import OpportunityHub


@pytest.mark.asyncio
async def test_hub_sequences_messages_throttles_updates_and_closes_slow_clients() -> None:
    now = 100
    hub = OpportunityHub(now_ms=lambda: now)
    queue = hub.subscribe()

    await hub.publish("status.changed", {"ready": True})
    first = await queue.get()

    assert first == {
        "type": "status.changed",
        "sequence": 1,
        "data": {"ready": True},
    }
    for index in range(257):
        await hub.publish("heartbeat", {"index": index})
    assert await queue.get() is None
    assert hub.sequence == 258


@pytest.mark.asyncio
async def test_hub_emits_lifecycle_changes_and_throttles_numeric_upserts() -> None:
    now = 100
    hub = OpportunityHub(now_ms=lambda: now)
    queue = hub.subscribe()
    tracker = OpportunityTracker(lifecycle_id=lambda: "lifecycle-1")
    opened = tracker.apply_cycle(_cycle(1_000, _confirmed("510")))

    await hub.lifecycle_events(opened)
    assert (await queue.get())["type"] == "opportunity.upsert"
    await hub.active_updates(tracker.active())
    assert queue.empty()

    now = 350
    await hub.active_updates(tracker.active())
    update = await queue.get()
    assert update["type"] == "opportunity.upsert"
    assert update["sequence"] == 2

    closed = tracker.close_for_restart(2_000)
    await hub.lifecycle_events(closed)
    assert (await queue.get())["type"] == "opportunity.closed"
