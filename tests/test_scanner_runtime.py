import pytest

from tests.test_storage import _confirmed, _cycle
from tri_arb.config import Settings
from tri_arb.scanner.runtime import ScannerRuntime
from tri_arb.storage import OpportunityStore


@pytest.mark.asyncio
async def test_runtime_persists_lifecycle_events_and_closes_active_on_clean_stop(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"
    settings = Settings(database_url=database_url, _env_file=None)
    observed = []

    async def on_events(events) -> None:
        observed.extend(events)

    runtime = ScannerRuntime(settings, now_ms=lambda: 500, on_events=on_events)
    await runtime.start()
    opened = await runtime.process_cycle(_cycle(1_000, _confirmed("510")))

    assert len(opened) == 1
    assert runtime.status().active_count == 1
    assert runtime.status().persisted_event_count == 1
    await runtime.stop()
    assert runtime.status().started is False
    assert len(observed) == 2

    reader = OpportunityStore(database_url)
    assert await reader.start(started_at_ms=2_000) == 0
    (lifecycle,) = await reader.list_lifecycles()
    events = await reader.list_events()
    assert lifecycle.state == "closed"
    assert lifecycle.close_reason == "process_restart"
    assert tuple(event.event_type for event in events) == ("opened", "closed")
    await reader.stop()


@pytest.mark.asyncio
async def test_runtime_exposes_depth_confirmed_near_misses_and_rolling_distribution(
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'diagnostics.db'}",
        _env_file=None,
    )
    runtime = ScannerRuntime(settings, now_ms=lambda: 1_000)
    await runtime.start()
    await runtime.process_cycle(_cycle(1_000, _confirmed("501")))

    diagnostics = runtime.status().diagnostics
    assert diagnostics is not None
    assert diagnostics.depth_confirmed_count == 1
    assert diagnostics.rolling_confirmed_sample_count == 1
    assert diagnostics.rolling_max_net_return_bps == 15
    assert diagnostics.rolling_buckets == (
        ("negative", 0),
        ("0_to_5", 0),
        ("5_to_10", 0),
        ("10_to_threshold", 1),
        ("opportunity", 0),
    )
    assert diagnostics.near_misses[0].net_return_bps == 15
    await runtime.stop()
