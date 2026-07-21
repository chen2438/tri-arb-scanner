"""Join scanner cycles, lifecycle transitions, persistence, and retention."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tri_arb.config import Settings
from tri_arb.market_data import MarketDataService
from tri_arb.scanner.engine import ScannerCycle, ScannerEngine
from tri_arb.scanner.lifecycle import LifecycleEvent, OpportunityLifecycle, OpportunityTracker
from tri_arb.storage.database import OpportunityStore

DAY_MS = 24 * 60 * 60 * 1_000


@dataclass(frozen=True, slots=True)
class ScannerRuntimeStatus:
    started: bool
    active_count: int
    last_cycle_ms: int | None
    persisted_event_count: int
    restart_closed_count: int
    last_cleanup_ms: int | None


OnEvents = Callable[[tuple[LifecycleEvent, ...]], Awaitable[None]]


async def _ignore_events(_events: tuple[LifecycleEvent, ...]) -> None:
    return None


class ScannerRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        store: OpportunityStore | None = None,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        on_events: OnEvents = _ignore_events,
    ) -> None:
        self._settings = settings
        self._store = store or OpportunityStore(settings.database_url)
        self._now_ms = now_ms
        self._on_events = on_events
        self._engine = ScannerEngine(settings, now_ms=now_ms)
        self._tracker = OpportunityTracker(
            open_threshold_bps=settings.min_net_return_bps,
            close_threshold_bps=settings.close_net_return_bps,
        )
        self._started = False
        self._last_cycle_ms: int | None = None
        self._event_count = 0
        self._restart_closed_count = 0
        self._last_cleanup_ms: int | None = None

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("scanner runtime is already started")
        now = self._now_ms()
        self._restart_closed_count = await self._store.start(started_at_ms=now)
        try:
            await self._store.cleanup(max(1, now - self._settings.history_retention_days * DAY_MS))
        except Exception:
            await self._store.stop()
            raise
        self._last_cleanup_ms = now
        self._started = True

    def active(self) -> tuple[OpportunityLifecycle, ...]:
        return self._tracker.active()

    def status(self) -> ScannerRuntimeStatus:
        return ScannerRuntimeStatus(
            started=self._started,
            active_count=len(self._tracker.active()),
            last_cycle_ms=self._last_cycle_ms,
            persisted_event_count=self._event_count,
            restart_closed_count=self._restart_closed_count,
            last_cleanup_ms=self._last_cleanup_ms,
        )

    async def process_cycle(self, cycle: ScannerCycle) -> tuple[LifecycleEvent, ...]:
        if not self._started:
            raise RuntimeError("scanner runtime is not started")
        lifecycle_events = self._tracker.apply_cycle(cycle)
        await self._store.record_events(lifecycle_events)
        self._last_cycle_ms = cycle.evaluated_at_ms
        self._event_count += len(lifecycle_events)
        if lifecycle_events:
            await self._on_events(lifecycle_events)
        now = self._now_ms()
        if self._last_cleanup_ms is None or now - self._last_cleanup_ms >= DAY_MS:
            await self._store.cleanup(max(1, now - self._settings.history_retention_days * DAY_MS))
            self._last_cleanup_ms = now
        return lifecycle_events

    async def cycle(self, market_data: MarketDataService) -> ScannerCycle:
        cycle = await self._engine.cycle(market_data)
        await self.process_cycle(cycle)
        return cycle

    async def run(self, market_data: MarketDataService, stop: asyncio.Event) -> None:
        await self.start()
        interval = self._settings.book_ticker_interval_ms / 1_000
        try:
            while not stop.is_set():
                await self.cycle(market_data)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    continue
        finally:
            await self.stop()

    async def stop(self) -> None:
        if not self._started:
            return
        now = max(self._now_ms(), self._last_cycle_ms or 0)
        closing_events = self._tracker.close_for_restart(now)
        try:
            await self._store.record_events(closing_events)
            self._event_count += len(closing_events)
            if closing_events:
                await self._on_events(closing_events)
        finally:
            await self._store.stop()
            self._started = False
