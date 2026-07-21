"""Independent async market-data service for public OKX spot data."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from tri_arb.config import Settings
from tri_arb.domain.coverage import CoreCoverage, select_core_coverage
from tri_arb.domain.graph import build_market_graph, enumerate_triangular_routes
from tri_arb.domain.models import (
    BookTicker,
    MarketActivity,
    MarketRules,
    PriceLimit,
    TriangularRoute,
)
from tri_arb.exchange.mexc import (
    DepthUpdate,
    ServerClock,
    SubscriptionPlan,
    WebSocketState,
    WebSocketStatus,
    reconcile_subscriptions,
)
from tri_arb.exchange.okx.metadata import NormalizedOkxInstruments
from tri_arb.exchange.okx.rest import NormalizedOkxTickers, OkxRestClient
from tri_arb.exchange.okx.websocket import OkxDepthWebSocketShard
from tri_arb.market_data import (
    CLOCK_INTERVAL_SECONDS,
    MARKET_ACTIVITY_INTERVAL_SECONDS,
    METADATA_INTERVAL_SECONDS,
    PRICE_REFERENCE_INTERVAL_SECONDS,
    SUBSCRIPTION_INTERVAL_SECONDS,
    MarketDataPhase,
    MarketDataSnapshot,
    MarketDataStatus,
)
from tri_arb.observability import get_logger, log_event

LOGGER = get_logger(__name__)


def _empty_plan() -> SubscriptionPlan:
    return SubscriptionPlan(leases=(), selected_route_ids=(), shards=((), ()))


class OkxMarketDataService:
    """Own OKX REST schedules, depth subscriptions, and coherent snapshots."""

    exchange = "OKX"

    def __init__(
        self,
        settings: Settings,
        *,
        rest_client: OkxRestClient | None = None,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._settings = settings
        self._rest = rest_client or OkxRestClient(
            settings.okx_rest_url,
            taker_commission=settings.okx_taker_commission,
        )
        self._owns_rest = rest_client is None
        self._now_ms = now_ms
        self._lock = asyncio.Lock()
        self._markets: tuple[MarketRules, ...] = ()
        self._routes: tuple[TriangularRoute, ...] = ()
        self._ranked_routes: tuple[TriangularRoute, ...] = ()
        self._tickers: dict[str, BookTicker] = {}
        self._activities: dict[str, MarketActivity] = {}
        self._price_limits: dict[str, PriceLimit] = {}
        self._core = CoreCoverage((), (), ())
        self._depth_updates: dict[str, DepthUpdate] = {}
        self._clock: ServerClock | None = None
        self._plan = _empty_plan()
        self._metadata_rejections = 0
        self._ticker_rejections = 0
        self._last_metadata_ms: int | None = None
        self._last_clock_ms: int | None = None
        self._last_ticker_ms: int | None = None
        self._last_activity_ms: int | None = None
        self._last_price_limit_ms: int | None = None
        self._errors: dict[str, str] = {}
        self._stopped = False
        self._websocket_statuses: dict[int, WebSocketStatus] = {}
        self._shards = (
            OkxDepthWebSocketShard(
                settings.okx_ws_url, 0, self._accept_depth, on_status=self._accept_ws_status
            ),
            OkxDepthWebSocketShard(
                settings.okx_ws_url, 1, self._accept_depth, on_status=self._accept_ws_status
            ),
        )

    async def _record_error(self, component: str, error: Exception) -> None:
        async with self._lock:
            self._errors[component] = f"{type(error).__name__}: {error}"
        log_event(
            LOGGER,
            "okx_market_data.error",
            level=logging.WARNING,
            component=component,
            error_type=type(error).__name__,
            error=str(error),
        )

    async def _clear_error(self, component: str) -> None:
        async with self._lock:
            self._errors.pop(component, None)

    async def refresh_metadata(self) -> NormalizedOkxInstruments:
        result = await self._rest.instruments()
        graph = build_market_graph(result.markets)
        routes = tuple(
            sorted(
                (
                    route
                    for anchor in self._settings.anchor_assets
                    for route in enumerate_triangular_routes(graph, anchor_asset=anchor)
                ),
                key=lambda route: route.route_id,
            )
        )
        symbols = {market.symbol for market in result.markets}
        route_ids = {route.route_id for route in routes}
        async with self._lock:
            self._markets = result.markets
            self._routes = routes
            self._ranked_routes = tuple(
                route for route in self._ranked_routes if route.route_id in route_ids
            )
            self._tickers = {key: value for key, value in self._tickers.items() if key in symbols}
            self._activities = {
                key: value for key, value in self._activities.items() if key in symbols
            }
            self._depth_updates = {
                key: value for key, value in self._depth_updates.items() if key in symbols
            }
            self._price_limits = {
                key: value for key, value in self._price_limits.items() if key in symbols
            }
            self._metadata_rejections = len(result.rejections)
            self._last_metadata_ms = self._now_ms()
        await self._rebuild_core()
        log_event(
            LOGGER,
            "okx_market_data.metadata_refreshed",
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

    async def refresh_tickers(self) -> NormalizedOkxTickers:
        result = await self._rest.tickers()
        async with self._lock:
            symbols = {market.symbol for market in self._markets}
            self._tickers = {
                item.symbol: item
                for item in result.tickers
                if not symbols or item.symbol in symbols
            }
            self._activities = {
                item.symbol: item
                for item in result.activities
                if not symbols or item.symbol in symbols
            }
            self._ticker_rejections = len(result.rejections)
            self._last_ticker_ms = self._now_ms()
            rebuild = not self._core.symbols
            if self._last_activity_ms is None or self._now_ms() - self._last_activity_ms >= int(
                MARKET_ACTIVITY_INTERVAL_SECONDS * 1000
            ):
                rebuild = True
            self._last_activity_ms = self._now_ms()
        if rebuild:
            await self._rebuild_core()
        return result

    async def _rebuild_core(self) -> CoreCoverage:
        async with self._lock:
            routes = self._routes
            tickers = dict(self._tickers)
            activities = dict(self._activities)
        coverage = select_core_coverage(routes, tickers, activities)
        async with self._lock:
            if {route.route_id for route in routes} == {
                route.route_id for route in self._routes
            }:
                self._core = coverage
        return coverage

    async def set_ranked_routes(self, routes: tuple[TriangularRoute, ...]) -> None:
        async with self._lock:
            valid = {route.route_id for route in self._routes}
            self._ranked_routes = tuple(route for route in routes if route.route_id in valid)

    async def refresh_price_limits(self) -> tuple[PriceLimit, ...]:
        async with self._lock:
            symbols = tuple(sorted(self._plan.symbols))
        concurrency = asyncio.Semaphore(10)

        async def fetch(symbol: str) -> PriceLimit:
            async with concurrency:
                return await self._rest.price_limit(symbol)

        limits = tuple(await asyncio.gather(*(fetch(symbol) for symbol in symbols)))
        async with self._lock:
            current_symbols = set(self._plan.symbols)
            retained = {
                symbol: value
                for symbol, value in self._price_limits.items()
                if symbol in current_symbols
            }
            retained.update(
                {value.symbol: value for value in limits if value.symbol in current_symbols}
            )
            self._price_limits = retained
            self._last_price_limit_ms = self._now_ms() if limits else None
        return limits

    async def reconcile_depth(self) -> SubscriptionPlan:
        async with self._lock:
            plan = reconcile_subscriptions(
                self._ranked_routes,
                self._plan.leases,
                now_ms=self._now_ms(),
                route_limit=self._settings.shortlist_routes,
                core_symbols=self._core.symbols,
            )
            previous = {
                symbol: index for index, shard in enumerate(self._plan.shards) for symbol in shard
            }
            current = {
                symbol: index for index, shard in enumerate(plan.shards) for symbol in shard
            }
            self._depth_updates = {
                symbol: update
                for symbol, update in self._depth_updates.items()
                if previous.get(symbol) == current.get(symbol)
            }
            self._price_limits = {
                symbol: value
                for symbol, value in self._price_limits.items()
                if symbol in current
            }
            self._plan = plan
            for index, symbols in enumerate(plan.shards):
                self._shards[index].set_symbols(symbols)
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
            if previous is not None and int(update.book.version) <= int(previous.book.version):
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
                symbols = set(self._plan.shards[status.shard_id])
                self._depth_updates = {
                    symbol: update
                    for symbol, update in self._depth_updates.items()
                    if symbol not in symbols
                }
            self._websocket_statuses[status.shard_id] = status

    def _phase(self) -> MarketDataPhase:
        if self._stopped:
            return MarketDataPhase.STOPPED
        if self._errors:
            return MarketDataPhase.DEGRADED
        for shard_id, symbols in enumerate(self._plan.shards):
            if symbols and (
                (status := self._websocket_statuses.get(shard_id)) is None
                or status.state is not WebSocketState.CONNECTED
            ):
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
                price_reference_count=len(self._price_limits),
                market_activity_count=len(self._activities),
                core_market_count=len(self._core.symbols),
                core_route_count=len(self._core.covered_route_ids),
                depth_book_count=len(self._depth_updates),
                subscription_count=len(self._plan.leases),
                metadata_rejection_count=self._metadata_rejections,
                ticker_rejection_count=self._ticker_rejections,
                market_activity_rejection_count=self._ticker_rejections,
                last_metadata_ms=self._last_metadata_ms,
                last_clock_ms=self._last_clock_ms,
                last_ticker_ms=self._last_ticker_ms,
                last_price_reference_ms=self._last_price_limit_ms,
                last_market_activity_ms=self._last_activity_ms,
                last_error="; ".join(
                    f"{key}={self._errors[key]}" for key in sorted(self._errors)
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
                price_references={},
                price_limits=dict(self._price_limits),
                depth_books={key: value.book for key, value in self._depth_updates.items()},
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
            except Exception as error:
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
                    self._periodic(
                        "price_limit",
                        self.refresh_price_limits,
                        PRICE_REFERENCE_INTERVAL_SECONDS,
                        stop,
                    )
                )
                tasks.create_task(
                    self._periodic("clock", self.calibrate_clock, CLOCK_INTERVAL_SECONDS, stop)
                )
                tasks.create_task(
                    self._periodic(
                        "ticker",
                        self.refresh_tickers,
                        self._settings.book_ticker_interval_ms / 1000,
                        stop,
                    )
                )
                tasks.create_task(
                    self._periodic(
                        "subscriptions", self.reconcile_depth, SUBSCRIPTION_INTERVAL_SECONDS, stop
                    )
                )
                for shard in self._shards:
                    tasks.create_task(shard.run(stop))
        finally:
            if self._owns_rest:
                await self._rest.aclose()
            async with self._lock:
                self._stopped = True
