"""Immutable exchange-neutral models used by the scanner."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")


class ConversionSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RejectReason(StrEnum):
    NON_POSITIVE_INPUT = "non_positive_input"
    MISSING_BOOK = "missing_book"
    INVALID_BOOK = "invalid_book"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    BELOW_MIN_BASE = "below_min_base"
    BELOW_MIN_QUOTE = "below_min_quote"
    ABOVE_MAX_QUOTE = "above_max_quote"
    MISSING_PRICE_REFERENCE = "missing_price_reference"
    STALE_PRICE_REFERENCE = "stale_price_reference"
    PRICE_PROTECTION = "price_protection"
    INVALID_RULE = "invalid_rule"


@dataclass(frozen=True, slots=True)
class PriceProtection:
    """Exchange-neutral maximum deviation from a public reference price."""

    max_buy_deviation: Decimal
    max_sell_deviation: Decimal

    def __post_init__(self) -> None:
        values = (self.max_buy_deviation, self.max_sell_deviation)
        if not all(value.is_finite() and ZERO <= value < Decimal("1") for value in values):
            raise ValueError("price protection deviations must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class MarketRules:
    symbol: str
    base_asset: str
    quote_asset: str
    base_asset_precision: int
    min_base_quantity: Decimal
    min_quote_amount: Decimal
    max_quote_amount: Decimal
    taker_commission: Decimal
    allowed_sides: frozenset[ConversionSide]
    price_protection: PriceProtection | None = None
    exchange: str = "MEXC"

    def __post_init__(self) -> None:
        if not self.symbol or not self.base_asset or not self.quote_asset or not self.exchange:
            raise ValueError("market identity fields cannot be empty")
        if self.exchange != self.exchange.strip().upper():
            raise ValueError("exchange identity must be uppercase without surrounding whitespace")
        if self.base_asset == self.quote_asset:
            raise ValueError("base and quote assets must differ")
        if not 0 <= self.base_asset_precision <= 30:
            raise ValueError("base asset precision must be between 0 and 30")
        decimal_fields = (
            self.min_base_quantity,
            self.min_quote_amount,
            self.max_quote_amount,
            self.taker_commission,
        )
        if not all(value.is_finite() for value in decimal_fields):
            raise ValueError("market decimal rules must be finite")
        if self.min_base_quantity <= ZERO or self.min_quote_amount <= ZERO:
            raise ValueError("minimum order rules must be positive")
        if self.max_quote_amount < self.min_quote_amount:
            raise ValueError("maximum quote amount cannot be below the minimum")
        if not ZERO <= self.taker_commission < Decimal("1"):
            raise ValueError("taker commission must be in [0, 1)")
        if not self.allowed_sides:
            raise ValueError("an enabled market must allow at least one side")
        if not self.allowed_sides <= {ConversionSide.BUY, ConversionSide.SELL}:
            raise ValueError("market contains an unknown side")

    @property
    def base_quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.base_asset_precision)


@dataclass(frozen=True, slots=True)
class PriceReference:
    symbol: str
    price: Decimal
    window_minutes: int
    received_time_ms: int

    def __post_init__(self) -> None:
        if (
            not self.symbol
            or self.symbol != self.symbol.strip()
            or self.symbol != self.symbol.upper()
        ):
            raise ValueError("invalid price reference symbol")
        if not self.price.is_finite() or self.price <= ZERO:
            raise ValueError("price reference must be finite and positive")
        if not 1 <= self.window_minutes <= 60:
            raise ValueError("price reference window must be between 1 and 60 minutes")
        if self.received_time_ms <= 0:
            raise ValueError("price reference receive time must be positive")


@dataclass(frozen=True, slots=True)
class ConversionEdge:
    market: MarketRules
    from_asset: str
    to_asset: str
    side: ConversionSide

    def __post_init__(self) -> None:
        expected = {
            ConversionSide.BUY: (self.market.quote_asset, self.market.base_asset),
            ConversionSide.SELL: (self.market.base_asset, self.market.quote_asset),
        }[self.side]
        if (self.from_asset, self.to_asset) != expected:
            raise ValueError("edge assets do not match its market and side")
        if self.side not in self.market.allowed_sides:
            raise ValueError("edge side is not allowed by the market")


@dataclass(frozen=True, slots=True)
class TriangularRoute:
    route_id: str
    assets: tuple[str, str, str, str]
    edges: tuple[ConversionEdge, ConversionEdge, ConversionEdge]

    def __post_init__(self) -> None:
        if self.assets[0] != self.assets[3]:
            raise ValueError("route must return to its anchor asset")
        if len(set(self.assets[:3])) != 3:
            raise ValueError("route must contain three distinct assets")
        if len({edge.market.symbol for edge in self.edges}) != 3:
            raise ValueError("route must use three distinct markets")
        if len({edge.market.exchange for edge in self.edges}) != 1:
            raise ValueError("route cannot combine markets from different exchanges")
        for index, edge in enumerate(self.edges):
            if (edge.from_asset, edge.to_asset) != (self.assets[index], self.assets[index + 1]):
                raise ValueError("route edges must follow the declared asset order")

    @property
    def exchange(self) -> str:
        return self.edges[0].market.exchange


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.price.is_finite() or not self.quantity.is_finite():
            raise ValueError("book levels must be finite")
        if self.price <= ZERO or self.quantity <= ZERO:
            raise ValueError("book levels must be positive")


@dataclass(frozen=True, slots=True)
class OrderBook:
    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    version: str
    source_time_ms: int
    received_time_ms: int

    def __post_init__(self) -> None:
        if not self.symbol or not self.version:
            raise ValueError("book identity fields cannot be empty")
        if self.source_time_ms <= 0 or self.received_time_ms <= 0:
            raise ValueError("book timestamps must be positive")
        if any(
            left.price < right.price for left, right in zip(self.bids, self.bids[1:], strict=False)
        ):
            raise ValueError("bids must be sorted from highest to lowest")
        if any(
            left.price > right.price for left, right in zip(self.asks, self.asks[1:], strict=False)
        ):
            raise ValueError("asks must be sorted from lowest to highest")
        if len({level.price for level in self.bids}) != len(self.bids):
            raise ValueError("bid prices must be unique")
        if len({level.price for level in self.asks}) != len(self.asks):
            raise ValueError("ask prices must be unique")


@dataclass(frozen=True, slots=True)
class BookTicker:
    """REST top-of-book input used only for broad route screening."""

    symbol: str
    bid_price: Decimal
    bid_quantity: Decimal
    ask_price: Decimal
    ask_quantity: Decimal
    received_time_ms: int

    def __post_init__(self) -> None:
        if (
            not self.symbol
            or self.symbol != self.symbol.strip()
            or self.symbol != self.symbol.upper()
        ):
            raise ValueError("invalid book ticker symbol")
        values = (self.bid_price, self.bid_quantity, self.ask_price, self.ask_quantity)
        if not all(value.is_finite() and value > ZERO for value in values):
            raise ValueError("book ticker prices and quantities must be finite and positive")
        if self.bid_price >= self.ask_price:
            raise ValueError("book ticker must have a positive spread")
        if self.received_time_ms <= 0:
            raise ValueError("book ticker receive time must be positive")


@dataclass(frozen=True, slots=True)
class MarketActivity:
    """Public rolling activity used only to choose persistent depth coverage."""

    symbol: str
    quote_volume: Decimal
    received_time_ms: int

    def __post_init__(self) -> None:
        if (
            not self.symbol
            or self.symbol != self.symbol.strip()
            or self.symbol != self.symbol.upper()
        ):
            raise ValueError("invalid market activity symbol")
        if not self.quote_volume.is_finite() or self.quote_volume < ZERO:
            raise ValueError("market activity volume must be finite and non-negative")
        if self.received_time_ms <= 0:
            raise ValueError("market activity receive time must be positive")


@dataclass(frozen=True, slots=True)
class LegSimulation:
    symbol: str
    side: ConversionSide
    from_asset: str
    to_asset: str
    input_amount: Decimal
    output_amount: Decimal
    average_price: Decimal
    fee_rate: Decimal
    fee_amount: Decimal
    dust_amount: Decimal
    levels_consumed: int
    book_version: str
    source_time_ms: int
    received_time_ms: int
    price_reference: Decimal | None = None
    price_protection_limit: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RouteSimulation:
    route: TriangularRoute
    start_amount: Decimal
    final_amount: Decimal
    gross_return_bps: Decimal
    modeled_return_bps: Decimal
    safety_buffer_bps: Decimal
    net_return_bps: Decimal
    estimated_profit: Decimal
    legs: tuple[LegSimulation, LegSimulation, LegSimulation]


@dataclass(frozen=True, slots=True)
class SimulationOutcome:
    simulation: RouteSimulation | None
    reject_reasons: tuple[RejectReason, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.simulation is not None
