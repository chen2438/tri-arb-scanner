"""Normalize MEXC exchangeInfo payloads into domain market rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import ConversionSide, MarketRules, PriceProtection

MAX_MARKETS = 10_000
MAX_IDENTITY_LENGTH = 64
MAX_DECIMAL_LENGTH = 128
MAX_FILTERS = 32


class MexcMetadataError(ValueError):
    """Raised when the exchangeInfo envelope is structurally unusable."""


@dataclass(frozen=True, slots=True)
class MarketMetadataRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedExchangeInfo:
    markets: tuple[MarketRules, ...]
    rejections: tuple[MarketMetadataRejection, ...]


def _decimal(raw: Mapping[str, Any], field: str) -> Decimal:
    raw_value = raw.get(field)
    if (
        isinstance(raw_value, bool)
        or not isinstance(raw_value, (str, int))
        or len(str(raw_value)) > MAX_DECIMAL_LENGTH
    ):
        raise MexcMetadataError(f"invalid {field}")
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as error:
        raise MexcMetadataError(f"invalid {field}") from error
    if not value.is_finite() or (value and abs(value.adjusted()) > 60):
        raise MexcMetadataError(f"invalid {field}")
    return value


def _integer(raw: Mapping[str, Any], field: str) -> int:
    value = raw.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or len(str(value)) > MAX_DECIMAL_LENGTH
    ):
        raise MexcMetadataError(f"invalid {field}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise MexcMetadataError(f"invalid {field}") from error
    if not number.is_finite() or number != number.to_integral_value():
        raise MexcMetadataError(f"invalid {field}")
    return int(number)


def _identity(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_IDENTITY_LENGTH
        or value != value.strip()
        or value != value.upper()
    ):
        raise MexcMetadataError(f"invalid {field}")
    return value


def _status(raw: Mapping[str, Any]) -> bool:
    status = raw.get("status")
    if isinstance(status, bool):
        raise MexcMetadataError("unknown status")
    if status in {"1", 1, "ENABLED"}:
        return True
    if status in {"2", 2, "3", 3, "PAUSED", "OFFLINE"}:
        return False
    raise MexcMetadataError("unknown status")


def _allowed_sides(raw: Mapping[str, Any]) -> frozenset[ConversionSide]:
    side_type = str(raw.get("tradeSideType"))
    if side_type == "1":
        return frozenset({ConversionSide.BUY, ConversionSide.SELL})
    if side_type == "2":
        return frozenset({ConversionSide.BUY})
    if side_type == "3":
        return frozenset({ConversionSide.SELL})
    if side_type == "4":
        return frozenset()
    raise MexcMetadataError("unknown tradeSideType")


def _min_base_quantity(raw: Mapping[str, Any], base_asset_precision: int) -> Decimal:
    explicit_minimum = _decimal(raw, "baseSizePrecision")
    if explicit_minimum == 0:
        # MEXC currently publishes zero for many enabled markets. Treat it as
        # "no stricter minimum" and retain the smallest representable quantity.
        return Decimal(1).scaleb(-base_asset_precision)
    return explicit_minimum


def _price_protection(raw: Mapping[str, Any]) -> PriceProtection | None:
    filters = raw.get("filters", [])
    if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
        raise MexcMetadataError("invalid filters")
    protection: PriceProtection | None = None
    for item in filters:
        if not isinstance(item, Mapping):
            raise MexcMetadataError("invalid filter")
        filter_type = item.get("filterType")
        if filter_type != "PERCENT_PRICE_BY_SIDE":
            raise MexcMetadataError("unknown filterType")
        if protection is not None:
            raise MexcMetadataError("duplicate price protection filter")
        try:
            protection = PriceProtection(
                max_buy_deviation=_decimal(item, "bidMultiplierUp"),
                max_sell_deviation=_decimal(item, "askMultiplierDown"),
            )
        except ValueError as error:
            raise MexcMetadataError(str(error)) from error
    return protection


def _normalize_symbol(raw: Mapping[str, Any]) -> MarketRules | None:
    if not _status(raw):
        return None
    spot_allowed = raw.get("isSpotTradingAllowed")
    if not isinstance(spot_allowed, bool):
        raise MexcMetadataError("invalid isSpotTradingAllowed")
    if not spot_allowed:
        return None
    allowed_sides = _allowed_sides(raw)
    if not allowed_sides:
        return None
    base_asset_precision = _integer(raw, "baseAssetPrecision")
    try:
        return MarketRules(
            symbol=_identity(raw, "symbol"),
            base_asset=_identity(raw, "baseAsset"),
            quote_asset=_identity(raw, "quoteAsset"),
            base_asset_precision=base_asset_precision,
            min_base_quantity=_min_base_quantity(raw, base_asset_precision),
            min_quote_amount=_decimal(raw, "quoteAmountPrecision"),
            max_quote_amount=_decimal(raw, "maxQuoteAmount"),
            taker_commission=_decimal(raw, "takerCommission"),
            allowed_sides=allowed_sides,
            price_protection=_price_protection(raw),
        )
    except ValueError as error:
        if isinstance(error, MexcMetadataError):
            raise
        raise MexcMetadataError(str(error)) from error


def normalize_exchange_info(payload: Mapping[str, Any]) -> NormalizedExchangeInfo:
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise MexcMetadataError("exchangeInfo symbols must be a list")
    if len(symbols) > MAX_MARKETS:
        raise MexcMetadataError("exchangeInfo exceeds the market limit")
    markets: list[MarketRules] = []
    rejections: list[MarketMetadataRejection] = []
    seen: set[str] = set()
    for index, raw in enumerate(symbols):
        if not isinstance(raw, Mapping):
            raise MexcMetadataError(f"symbol at index {index} must be an object")
        raw_symbol = raw.get("symbol")
        if isinstance(raw_symbol, str) and len(raw_symbol) <= MAX_IDENTITY_LENGTH:
            if raw_symbol in seen:
                raise MexcMetadataError(f"duplicate symbol: {raw_symbol}")
            seen.add(raw_symbol)
        try:
            market = _normalize_symbol(raw)
        except MexcMetadataError as error:
            symbol = str(raw.get("symbol", f"index {index}"))[:MAX_IDENTITY_LENGTH]
            rejections.append(MarketMetadataRejection(symbol, str(error)))
            continue
        if market is None:
            continue
        markets.append(market)
    return NormalizedExchangeInfo(
        markets=tuple(sorted(markets, key=lambda market: market.symbol)),
        rejections=tuple(sorted(rejections, key=lambda rejection: rejection.symbol)),
    )
