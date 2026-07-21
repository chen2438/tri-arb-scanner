"""Orchestrate broad screening and current-generation depth confirmation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from tri_arb.config import Settings
from tri_arb.market_data import (
    MarketDataService,
    MarketDataSnapshot,
    is_fresh_timestamp,
    ticker_freshness_ms,
)
from tri_arb.scanner.confirmation import (
    ConfirmationOutcome,
    ConfirmationRejectReason,
    confirm_candidate,
)
from tri_arb.scanner.screening import (
    BroadCandidate,
    BroadScreenResult,
    screen_routes_multi_anchor,
)


@dataclass(frozen=True, slots=True)
class ScannerCycle:
    evaluated_at_ms: int
    broad_candidates: tuple[BroadCandidate, ...]
    confirmations: tuple[ConfirmationOutcome, ...]
    broad_screen: BroadScreenResult | None = None

    @property
    def confirmed(self) -> tuple[ConfirmationOutcome, ...]:
        return tuple(outcome for outcome in self.confirmations if outcome.accepted)


OnCycle = Callable[[ScannerCycle], Awaitable[None]]


class MarketDataSource(Protocol):
    exchange: str

    async def snapshot(self) -> MarketDataSnapshot: ...

    async def set_ranked_routes(self, routes: tuple) -> None: ...


async def _ignore_cycle(_cycle: ScannerCycle) -> None:
    return None


class ScannerEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._settings = settings
        self._now_ms = now_ms

    def evaluate(self, snapshot: MarketDataSnapshot) -> ScannerCycle:
        evaluated_at_ms = self._now_ms()
        max_ticker_age_ms = ticker_freshness_ms(self._settings)
        fresh_tickers = {
            symbol: ticker
            for symbol, ticker in snapshot.tickers.items()
            if is_fresh_timestamp(
                ticker.received_time_ms,
                now_ms=evaluated_at_ms,
                max_age_ms=max_ticker_age_ms,
            )
        }
        broad_screen = screen_routes_multi_anchor(
            snapshot.routes,
            fresh_tickers,
            {anchor: self._settings.notional for anchor in self._settings.anchor_assets},
            limit=self._settings.shortlist_routes,
        )
        broad = broad_screen.candidates
        if snapshot.clock is None:
            confirmations = tuple(
                ConfirmationOutcome(
                    candidate,
                    None,
                    None,
                    None,
                    (ConfirmationRejectReason.CLOCK_UNAVAILABLE.value,),
                )
                for candidate in broad
            )
        else:
            server_time_ms = snapshot.clock.server_time_ms(evaluated_at_ms)
            confirmations = tuple(
                confirm_candidate(
                    candidate,
                    snapshot.depth_updates,
                    snapshot.subscription_plan,
                    snapshot.status.websocket_statuses,
                    server_time_ms=server_time_ms,
                    safety_buffer_bps=self._settings.safety_buffer_bps,
                    price_references=snapshot.price_references,
                    price_limits=snapshot.price_limits,
                    local_time_ms=evaluated_at_ms,
                    max_age_ms=self._settings.max_depth_age_ms,
                    max_leg_skew_ms=self._settings.max_leg_skew_ms,
                )
                for candidate in broad
            )
        return ScannerCycle(evaluated_at_ms, broad, confirmations, broad_screen)

    async def cycle(self, market_data: MarketDataSource) -> ScannerCycle:
        snapshot = await market_data.snapshot()
        cycle = self.evaluate(snapshot)
        await market_data.set_ranked_routes(
            tuple(candidate.route for candidate in cycle.broad_candidates)
        )
        return cycle

    async def cycle_many(self, market_data: Sequence[MarketDataSource]) -> ScannerCycle:
        if not market_data:
            raise ValueError("multi-exchange scan requires at least one market-data source")
        snapshots = await asyncio.gather(*(source.snapshot() for source in market_data))
        cycles = tuple(self.evaluate(snapshot) for snapshot in snapshots)
        await asyncio.gather(
            *(
                source.set_ranked_routes(
                    tuple(candidate.route for candidate in cycle.broad_candidates)
                )
                for source, cycle in zip(market_data, cycles, strict=True)
            )
        )
        candidates = tuple(
            sorted(
                (candidate for cycle in cycles for candidate in cycle.broad_candidates),
                key=lambda item: (-item.estimated_return_bps, item.route.route_id),
            )
        )
        confirmations = tuple(
            outcome for cycle in cycles for outcome in cycle.confirmations
        )
        screens = tuple(cycle.broad_screen for cycle in cycles if cycle.broad_screen is not None)
        best_values = tuple(
            screen.best_estimated_return_bps
            for screen in screens
            if screen.best_estimated_return_bps is not None
        )
        broad_screen = BroadScreenResult(
            candidates=candidates,
            total_route_count=sum(screen.total_route_count for screen in screens),
            priced_route_count=sum(screen.priced_route_count for screen in screens),
            positive_route_count=sum(screen.positive_route_count for screen in screens),
            best_estimated_return_bps=max(best_values, default=None),
        )
        return ScannerCycle(
            evaluated_at_ms=max(cycle.evaluated_at_ms for cycle in cycles),
            broad_candidates=candidates,
            confirmations=confirmations,
            broad_screen=broad_screen,
        )

    async def run(
        self,
        market_data: MarketDataService,
        stop: asyncio.Event,
        *,
        on_cycle: OnCycle = _ignore_cycle,
    ) -> None:
        interval = self._settings.book_ticker_interval_ms / 1000
        while not stop.is_set():
            await on_cycle(await self.cycle(market_data))
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue
