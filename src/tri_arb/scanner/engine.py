"""Orchestrate broad screening and current-generation depth confirmation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tri_arb.config import Settings
from tri_arb.market_data import MarketDataService, MarketDataSnapshot
from tri_arb.scanner.confirmation import (
    ConfirmationOutcome,
    ConfirmationRejectReason,
    confirm_candidate,
)
from tri_arb.scanner.screening import BroadCandidate, screen_routes


@dataclass(frozen=True, slots=True)
class ScannerCycle:
    evaluated_at_ms: int
    broad_candidates: tuple[BroadCandidate, ...]
    confirmations: tuple[ConfirmationOutcome, ...]

    @property
    def confirmed(self) -> tuple[ConfirmationOutcome, ...]:
        return tuple(outcome for outcome in self.confirmations if outcome.accepted)


OnCycle = Callable[[ScannerCycle], Awaitable[None]]


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
        broad = screen_routes(
            snapshot.routes,
            snapshot.tickers,
            self._settings.notional,
            limit=self._settings.shortlist_routes,
        )
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
                    max_age_ms=self._settings.max_depth_age_ms,
                    max_leg_skew_ms=self._settings.max_leg_skew_ms,
                )
                for candidate in broad
            )
        return ScannerCycle(evaluated_at_ms, broad, confirmations)

    async def cycle(self, market_data: MarketDataService) -> ScannerCycle:
        snapshot = await market_data.snapshot()
        cycle = self.evaluate(snapshot)
        await market_data.set_ranked_routes(
            tuple(candidate.route for candidate in cycle.broad_candidates)
        )
        return cycle

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
