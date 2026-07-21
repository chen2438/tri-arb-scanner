"""Async orchestration and coherent read-only state for MEXC market data."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from tri_arb.config import Settings
from tri_arb.domain.graph import build_market_graph, enumerate_triangular_routes
from tri_arb.domain.models import BookTicker, MarketRules, OrderBook, TriangularRoute
from tri_arb.exchange.mexc import (
    DepthUpdate,
    MexcDepthWebSocketShard,
    MexcRestClient,
    NormalizedBookTickers,
    NormalizedExchangeInfo,
    ServerClock,
    SubscriptionPlan,
    WebSocketState,
    WebSocketStatus,
    reconcile_subscriptions,
)
from tri_arb.observability import get_logger, log_event

METADATA_INTERVAL_SECONDS = 300.0
CLOCK_INTERVAL_SECONDS = 60.0
SUBSCRIPTION_INTERVAL_SECONDS = 5.0
LOGGER = get_logger(__name__)


class MarketDataPhase(StrEnum):
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class MarketDataStatus:
    phase: MarketDataPhase
    market_count: int
    route_count: int
    ticker_count: int
    depth_book_count: int
    subscription_count: int
    metadata_rejection_count: int
    ticker_rejection_count: int
    last_metadata_ms: int | None
    last_clock_ms: int | None
    last_ticker_ms: int | None
    last_error: str | None
    websocket_statuses: tuple[WebSocketStatus, ...]

    @property
    def ready(self) -> bool:
        return self.phase is MarketDataPhase.READY


@dataclass(frozen=True, slots=True)
class MarketDataSnapshot:
    status: MarketDataStatus
    markets: tuple[MarketRules, ...]
    routes: tuple[TriangularRoute, ...]
    tickers: Mapping[str, BookTicker]
    depth_books: Mapping[str, OrderBook]
    depth_updates: Mapping[str, DepthUpdate]
    clock: ServerClock | None
    subscription_plan: SubscriptionPlan


def _empty_plan() -> SubscriptionPlan:
    return SubscriptionPlan(leases=(), selected_route_ids=(), shards=((), ()))


class MarketDataService:
    """Own REST schedules, shortlist subscriptions, and coherent latest snapshots."""

    def __init__(
        self,
        settings: Settings,
        *,
        rest_client: MexcRestClient | None = None,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._settings = settings
        self._rest = rest_client or MexcRestClient(settings.mexc_rest_url)
        self._owns_rest = rest_client is None
        self._now_ms = now_ms
        self._lock = asyncio.Lock()
        self._markets: tuple[MarketRules, ...] = ()
        self._routes: tuple[TriangularRoute, ...] = ()
        self._ranked_routes: tuple[TriangularRoute, ...] = ()
        self._tickers: dict[str, BookTicker] = {}
        self._depth_updates: dict[str, DepthUpdate] = {}
        self._clock: ServerClock | None = None
        self._plan = _empty_plan()
        self._metadata_rejections = 0
        self._ticker_rejections = 0
        self._last_metadata_ms: int | None = None
        self._last_clock_ms: int | None = None
        self._last_ticker_ms: int | None = None
        self._errors: dict[str, str] = {}
        self._stopped = False
        self._websocket_statuses: dict[int, WebSocketStatus] = {}
        self._shards = (
            MexcDepthWebSocketShard(
                settings.mexc_ws_url, 0, self._accept_depth, on_status=self._accept_ws_status
            ),
            MexcDepthWebSocketShard(
                settings.mexc_ws_url, 1, self._accept_depth, on_status=self._accept_ws_status
            ),
        )

    async def _record_error(self, component: str, error: Exception) -> None:
        async with self._lock:
            self._errors[component] = f"{type(error).__name__}: {error}"
        log_event(
            LOGGER,
            "market_data.error",
            level=logging.WARNING,
            component=component,
            error_type=type(error).__name__,
            error=str(error),
        )

    async def _clear_error(self, component: str) -> None:
        async with self._lock:
            self._errors.pop(component, None)

    async def refresh_metadata(self) -> NormalizedExchangeInfo:
        result = await self._rest.exchange_info()
        routes = enumerate_triangular_routes(
            build_market_graph(result.markets), anchor_asset=self._settings.anchor_asset
        )
        valid_symbols = {market.symbol for market in result.markets}
        route_ids = {route.route_id for route in routes}
        async with self._lock:
            self._markets = result.markets
            self._routes = routes
            self._ranked_routes = tuple(
                route for route in self._ranked_routes if route.route_id in route_ids
            )
            self._tickers = {
                symbol: ticker
                for symbol, ticker in self._tickers.items()
                if symbol in valid_symbols
            }
            self._depth_updates = {
                symbol: update
                for symbol, update in self._depth_updates.items()
                if symbol in valid_symbols
            }
            self._metadata_rejections = len(result.rejections)
            self._last_metadata_ms = self._now_ms()
        log_event(
            LOGGER,
            "market_data.metadata_refreshed",
            market_count=len(result.markets),
            route_count=len(routes),
            metadata_rejection_count=len(result.rejections),
        )
        return result

    async def calibrate_clock(self) -> ServerClock:
        clock = await self._rest.calibrate_clock()
        async with self._lock:
            self._clock = clock
            self._last_clock_ms = self._now_ms()
        return clock

    async def refresh_tickers(self) -> NormalizedBookTickers:
        result = await self._rest.book_tickers()
        async with self._lock:
            valid_symbols = {market.symbol for market in self._markets}
            self._tickers = {
                ticker.symbol: ticker for ticker in result.tickers if ticker.symbol in valid_symbols
            }
            self._ticker_rejections = len(result.rejections)
            self._last_ticker_ms = self._now_ms()
        return result

    async def set_ranked_routes(self, routes: tuple[TriangularRoute, ...]) -> None:
        async with self._lock:
            valid = {route.route_id for route in self._routes}
            self._ranked_routes = tuple(route for route in routes if route.route_id in valid)

    async def reconcile_depth(self) -> SubscriptionPlan:
        async with self._lock:
            plan = reconcile_subscriptions(
                self._ranked_routes,
                self._plan.leases,
                now_ms=self._now_ms(),
                route_limit=self._settings.shortlist_routes,
            )
            previous_shards = {
                symbol: index for index, shard in enumerate(self._plan.shards) for symbol in shard
            }
            next_shards = {
                symbol: index for index, shard in enumerate(plan.shards) for symbol in shard
            }
            self._depth_updates = {
                symbol: update
                for symbol, update in self._depth_updates.items()
                if previous_shards.get(symbol) == next_shards.get(symbol)
            }
            self._plan = plan
            for index, shard in enumerate(plan.shards):
                self._shards[index].set_symbols(shard)
            return plan

    async def _accept_depth(self, update: DepthUpdate) -> None:
        async with self._lock:
            expected = self._plan.shards[update.shard_id]
            status = self._websocket_statuses.get(update.shard_id)
            if (
                update.book.symbol not in expected
                or status is None
                or status.state is not WebSocketState.CONNECTED
                or update.connection_generation != status.connection_generation
            ):
                return
            previous = self._depth_updates.get(update.book.symbol)
            if previous is not None and (
                update.book.source_time_ms < previous.book.source_time_ms
                or (
                    update.book.source_time_ms == previous.book.source_time_ms
                    and int(update.book.version) <= int(previous.book.version)
                )
            ):
                return
            self._depth_updates[update.book.symbol] = update

    async def _accept_ws_status(self, status: WebSocketStatus) -> None:
        async with self._lock:
            previous = self._websocket_statuses.get(status.shard_id)
            if (
                status.state is not WebSocketState.CONNECTED
                or previous is None
                or previous.connection_generation != status.connection_generation
            ):
                shard_symbols = set(self._plan.shards[status.shard_id])
                self._depth_updates = {
                    symbol: update
                    for symbol, update in self._depth_updates.items()
                    if symbol not in shard_symbols
                }
            self._websocket_statuses[status.shard_id] = status
        if previous is None or previous.state != status.state or previous.error != status.error:
            log_event(
                LOGGER,
                "market_data.websocket_state",
                level=logging.WARNING if status.state is WebSocketState.BACKOFF else logging.INFO,
                shard_id=status.shard_id,
                state=status.state.value,
                subscription_count=len(status.subscriptions),
                error=status.error,
            )

    def _phase(self) -> MarketDataPhase:
        if self._stopped:
            return MarketDataPhase.STOPPED
        if self._errors:
            return MarketDataPhase.DEGRADED
        for shard_id, symbols in enumerate(self._plan.shards):
            if not symbols:
                continue
            status = self._websocket_statuses.get(shard_id)
            if status is None or status.state is not WebSocketState.CONNECTED:
                return MarketDataPhase.DEGRADED
        if self._markets and self._tickers and self._clock is not None:
            return MarketDataPhase.READY
        return MarketDataPhase.INITIALIZING

    async def snapshot(self) -> MarketDataSnapshot:
        async with self._lock:
            status = MarketDataStatus(
                phase=self._phase(),
                market_count=len(self._markets),
                route_count=len(self._routes),
                ticker_count=len(self._tickers),
                depth_book_count=len(self._depth_updates),
                subscription_count=len(self._plan.leases),
                metadata_rejection_count=self._metadata_rejections,
                ticker_rejection_count=self._ticker_rejections,
                last_metadata_ms=self._last_metadata_ms,
                last_clock_ms=self._last_clock_ms,
                last_ticker_ms=self._last_ticker_ms,
                last_error="; ".join(
                    f"{component}={self._errors[component]}" for component in sorted(self._errors)
                )
                or None,
                websocket_statuses=tuple(
                    self._websocket_statuses[key] for key in sorted(self._websocket_statuses)
                ),
            )
            return MarketDataSnapshot(
                status=status,
                markets=self._markets,
                routes=self._routes,
                tickers=dict(self._tickers),
                depth_books={symbol: update.book for symbol, update in self._depth_updates.items()},
                depth_updates=dict(self._depth_updates),
                clock=self._clock,
                subscription_plan=self._plan,
            )

    async def _periodic(
        self,
        component: str,
        action: Callable[[], Awaitable[object]],
        interval_seconds: float,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await action()
                await self._clear_error(component)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # the next scheduled cycle must remain alive
                await self._record_error(component, error)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue

    async def run(self, stop: asyncio.Event) -> None:
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(
                    self._periodic(
                        "metadata", self.refresh_metadata, METADATA_INTERVAL_SECONDS, stop
                    )
                )
                tasks.create_task(
                    self._periodic("clock", self.calibrate_clock, CLOCK_INTERVAL_SECONDS, stop)
                )
                tasks.create_task(
                    self._periodic(
                        "book_ticker",
                        self.refresh_tickers,
                        self._settings.book_ticker_interval_ms / 1000,
                        stop,
                    )
                )
                tasks.create_task(
                    self._periodic(
                        "subscriptions",
                        self.reconcile_depth,
                        SUBSCRIPTION_INTERVAL_SECONDS,
                        stop,
                    )
                )
                for shard in self._shards:
                    tasks.create_task(shard.run(stop))
        finally:
            if self._owns_rest:
                await self._rest.aclose()
            async with self._lock:
                self._stopped = True
