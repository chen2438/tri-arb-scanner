"""Deterministic two-shard planning for shortlisted depth subscriptions."""

from __future__ import annotations

from dataclasses import dataclass

from tri_arb.domain.models import TriangularRoute

MAX_CONNECTIONS = 2
MAX_SUBSCRIPTIONS_PER_CONNECTION = 30
MAX_DEPTH_MARKETS = MAX_CONNECTIONS * MAX_SUBSCRIPTIONS_PER_CONNECTION
MIN_RESIDENCE_MS = 15_000


@dataclass(frozen=True, slots=True)
class MarketLease:
    symbol: str
    subscribed_at_ms: int

    def __post_init__(self) -> None:
        if (
            not self.symbol
            or self.symbol != self.symbol.strip()
            or self.symbol != self.symbol.upper()
        ):
            raise ValueError("invalid leased symbol")
        if self.subscribed_at_ms < 0:
            raise ValueError("subscription time cannot be negative")


@dataclass(frozen=True, slots=True)
class SubscriptionPlan:
    leases: tuple[MarketLease, ...]
    selected_route_ids: tuple[str, ...]
    shards: tuple[tuple[str, ...], tuple[str, ...]]
    core_symbols: tuple[str, ...] = ()

    @property
    def symbols(self) -> frozenset[str]:
        return frozenset(lease.symbol for lease in self.leases)


def reconcile_subscriptions(
    ranked_routes: tuple[TriangularRoute, ...],
    current_leases: tuple[MarketLease, ...],
    *,
    now_ms: int,
    route_limit: int = 20,
    minimum_residence_ms: int = MIN_RESIDENCE_MS,
    core_symbols: tuple[str, ...] = (),
) -> SubscriptionPlan:
    if now_ms < 0 or route_limit < 0 or minimum_residence_ms < 0:
        raise ValueError("subscription planning inputs cannot be negative")
    current = {lease.symbol: lease for lease in current_leases}
    if len(current) != len(current_leases):
        raise ValueError("current subscription leases must be unique")
    if len(current) > MAX_DEPTH_MARKETS:
        raise ValueError("current subscriptions exceed the hard market limit")
    if any(lease.subscribed_at_ms > now_ms for lease in current_leases):
        raise ValueError("subscription time cannot be in the future")
    core = set(core_symbols)
    if len(core) != len(core_symbols) or len(core) > MAX_DEPTH_MARKETS:
        raise ValueError("core subscriptions must be unique and within the market limit")

    target = core | {
        symbol
        for symbol, lease in current.items()
        if now_ms - lease.subscribed_at_ms < minimum_residence_ms
    }
    selected_route_ids: list[str] = []
    seen_routes: set[str] = set()
    for route in ranked_routes:
        if len(selected_route_ids) >= route_limit:
            break
        if route.route_id in seen_routes:
            continue
        seen_routes.add(route.route_id)
        route_symbols = {edge.market.symbol for edge in route.edges}
        if route_symbols <= target:
            selected_route_ids.append(route.route_id)
            continue
        if len(target | route_symbols) > MAX_DEPTH_MARKETS:
            continue
        target.update(route_symbols)
        selected_route_ids.append(route.route_id)

    leases = tuple(current.get(symbol, MarketLease(symbol, now_ms)) for symbol in sorted(target))
    symbols = tuple(lease.symbol for lease in leases)
    first = symbols[:MAX_SUBSCRIPTIONS_PER_CONNECTION]
    second = symbols[MAX_SUBSCRIPTIONS_PER_CONNECTION:]
    return SubscriptionPlan(
        leases=leases,
        selected_route_ids=tuple(selected_route_ids),
        shards=(first, second),
        core_symbols=tuple(sorted(core)),
    )
