"""Application runtime coordination and process-local WebSocket event bus."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from tri_arb.config import Settings
from tri_arb.market_data import MarketDataService, MarketDataSnapshot
from tri_arb.presentation import opportunity_to_public, utc_iso
from tri_arb.scanner.lifecycle import LifecycleEvent, LifecycleEventType, OpportunityLifecycle
from tri_arb.scanner.runtime import ScannerRuntime

HEARTBEAT_SECONDS = 15.0
STATUS_POLL_SECONDS = 1.0
UPSERT_THROTTLE_MS = 250


class OpportunityHub:
    def __init__(self, *, now_ms=lambda: time.time_ns() // 1_000_000) -> None:
        self._now_ms = now_ms
        self._sequence = 0
        self._clients: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._last_upsert_ms: dict[str, int] = {}

    @property
    def sequence(self) -> int:
        return self._sequence

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._clients.discard(queue)

    async def publish(self, message_type: str, data: Any) -> None:
        self._sequence += 1
        message = {"type": message_type, "sequence": self._sequence, "data": data}
        stale: list[asyncio.Queue[dict[str, Any] | None]] = []
        for queue in tuple(self._clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._clients.discard(queue)
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)

    async def lifecycle_events(self, events: tuple[LifecycleEvent, ...]) -> None:
        for lifecycle_event in events:
            lifecycle = lifecycle_event.lifecycle
            if lifecycle_event.event_type is LifecycleEventType.CLOSED:
                self._last_upsert_ms.pop(lifecycle.lifecycle_id, None)
                await self.publish("opportunity.closed", opportunity_to_public(lifecycle))
            else:
                self._last_upsert_ms[lifecycle.lifecycle_id] = self._now_ms()
                await self.publish("opportunity.upsert", opportunity_to_public(lifecycle))

    async def active_updates(self, active: tuple[OpportunityLifecycle, ...]) -> None:
        now = self._now_ms()
        active_ids = {lifecycle.lifecycle_id for lifecycle in active}
        self._last_upsert_ms = {
            lifecycle_id: updated_at
            for lifecycle_id, updated_at in self._last_upsert_ms.items()
            if lifecycle_id in active_ids
        }
        for lifecycle in active:
            last = self._last_upsert_ms.get(lifecycle.lifecycle_id)
            if last is not None and now - last < UPSERT_THROTTLE_MS:
                continue
            self._last_upsert_ms[lifecycle.lifecycle_id] = now
            await self.publish("opportunity.upsert", opportunity_to_public(lifecycle))


class ApplicationServices:
    def __init__(
        self,
        settings: Settings,
        *,
        market_data: MarketDataService | None = None,
        scanner_runtime: ScannerRuntime | None = None,
        now_ms=lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self.settings = settings
        self.now_ms = now_ms
        self.hub = OpportunityHub(now_ms=now_ms)
        self.market_data = market_data or MarketDataService(settings, now_ms=now_ms)
        self.scanner_runtime = scanner_runtime or ScannerRuntime(
            settings,
            now_ms=now_ms,
            on_events=self.hub.lifecycle_events,
            on_active=self.hub.active_updates,
        )
        self._stop = asyncio.Event()
        self._tasks: tuple[asyncio.Task[None], ...] = ()
        self._service_error: str | None = None

    async def start(self) -> None:
        if self._tasks:
            raise RuntimeError("application services are already started")
        self._stop.clear()
        self._tasks = (
            asyncio.create_task(self.market_data.run(self._stop), name="tri-arb-market-data"),
            asyncio.create_task(
                self.scanner_runtime.run(self.market_data, self._stop), name="tri-arb-scanner"
            ),
            asyncio.create_task(self._broadcast_status(), name="tri-arb-status-events"),
            asyncio.create_task(self._heartbeat(), name="tri-arb-heartbeat"),
        )
        for task in self._tasks:
            task.add_done_callback(self._capture_failure)

    def _capture_failure(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._service_error = f"{type(error).__name__}: {error}"
            self._stop.set()

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._stop.set()
        tasks, self._tasks = self._tasks, ()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        error = next((result for result in results if isinstance(result, Exception)), None)
        if error is not None:
            raise error

    async def market_snapshot(self) -> MarketDataSnapshot:
        return await self.market_data.snapshot()

    async def status_payload(self) -> dict[str, Any]:
        market = await self.market_data.snapshot()
        scanner = self.scanner_runtime.status()
        now = self.now_ms()

        def age(last_ms: int | None) -> int | None:
            return max(0, now - last_ms) if last_ms is not None else None

        service_error = self._service_error or market.status.last_error
        return {
            "phase": "degraded" if service_error is not None else market.status.phase.value,
            "ready": market.status.ready and scanner.started and self._service_error is None,
            "market_count": market.status.market_count,
            "route_count": market.status.route_count,
            "ticker_count": market.status.ticker_count,
            "depth_book_count": market.status.depth_book_count,
            "subscription_count": market.status.subscription_count,
            "active_opportunity_count": scanner.active_count,
            "rest_metadata_age_ms": age(market.status.last_metadata_ms),
            "rest_clock_age_ms": age(market.status.last_clock_ms),
            "rest_ticker_age_ms": age(market.status.last_ticker_ms),
            "last_scan_at": utc_iso(scanner.last_cycle_ms),
            "last_error": service_error,
            "websocket_connections": [
                {
                    "shard_id": status.shard_id,
                    "state": status.state.value,
                    "generation": status.connection_generation,
                    "subscription_count": len(status.subscriptions),
                    "error": status.error,
                }
                for status in market.status.websocket_statuses
            ],
        }

    async def snapshot_message(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "sequence": self.hub.sequence,
            "data": {
                "opportunities": [
                    opportunity_to_public(lifecycle) for lifecycle in self.scanner_runtime.active()
                ],
                "status": await self.status_payload(),
            },
        }

    async def _broadcast_status(self) -> None:
        previous: Mapping[str, Any] | None = None
        while not self._stop.is_set():
            current = await self.status_payload()
            if current != previous:
                await self.hub.publish("status.changed", current)
                previous = current
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=STATUS_POLL_SECONDS)
            except TimeoutError:
                continue

    async def _heartbeat(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                await self.hub.publish("heartbeat", {"server_time": utc_iso(self.now_ms())})
