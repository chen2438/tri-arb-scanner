"""Strict normalization of public Binance spot trading rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import ConversionSide, MarketRules, PriceProtection

MAX_MARKETS = 10_000
MAX_FILTERS = 32
MAX_RULES = 10_000
MAX_IDENTITY_LENGTH = 64
MAX_DECIMAL_LENGTH = 128


class BinanceMetadataError(ValueError):
    """Raised when Binance metadata is structurally unusable."""


@dataclass(frozen=True, slots=True)
class BinanceMetadataRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedBinanceExchangeInfo:
    markets: tuple[MarketRules, ...]
    rejections: tuple[BinanceMetadataRejection, ...]


def _identity(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_IDENTITY_LENGTH
        or value != value.strip().upper()
    ):
        raise BinanceMetadataError(f"invalid {field}")
    return value


def _decimal(raw: Mapping[str, Any], field: str, *, allow_zero: bool = False) -> Decimal:
    value = raw.get(field)
    if not isinstance(value, str) or not value or len(value) > MAX_DECIMAL_LENGTH:
        raise BinanceMetadataError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise BinanceMetadataError(f"invalid {field}") from error
    if (
        not result.is_finite()
        or result < 0
        or (result == 0 and not allow_zero)
        or (result and abs(result.adjusted()) > 60)
    ):
        raise BinanceMetadataError(f"invalid {field}")
    return result


def _power_of_ten_precision(step: Decimal) -> int:
    normalized = step.normalize()
    if normalized.as_tuple().digits != (1,) or normalized.adjusted() > 0:
        raise BinanceMetadataError("LOT_SIZE stepSize must be a power of ten")
    return max(0, -normalized.as_tuple().exponent)


def _indexed(items: Any, *, key: str, label: str, maximum: int) -> dict[str, Mapping[str, Any]]:
    if not isinstance(items, list) or len(items) > maximum:
        raise BinanceMetadataError(f"invalid {label}")
    result: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise BinanceMetadataError(f"{label} item at index {index} must be an object")
        name = item.get(key)
        if not isinstance(name, str) or not name or len(name) > MAX_IDENTITY_LENGTH:
            raise BinanceMetadataError(f"invalid {label} identity")
        if name in result:
            raise BinanceMetadataError(f"duplicate {label}: {name}")
        result[name] = item
    return result


def _protection(rule: Mapping[str, Any]) -> PriceProtection:
    rules = _indexed(rule.get("rules"), key="ruleType", label="execution rule", maximum=32)
    price_range = rules.get("PRICE_RANGE")
    if price_range is None:
        raise BinanceMetadataError("missing PRICE_RANGE execution rule")
    buy_up = _decimal(price_range, "bidLimitMultUp")
    sell_down = _decimal(price_range, "askLimitMultDown")
    if buy_up < 1 or sell_down > 1:
        raise BinanceMetadataError("invalid PRICE_RANGE execution bounds")
    try:
        return PriceProtection(buy_up - 1, 1 - sell_down)
    except ValueError as error:
        raise BinanceMetadataError(str(error)) from error


def _notional(filters: Mapping[str, Mapping[str, Any]]) -> tuple[Decimal, Decimal | None]:
    notional = filters.get("NOTIONAL")
    legacy = filters.get("MIN_NOTIONAL")
    if notional is not None and legacy is not None:
        raise BinanceMetadataError("duplicate notional filters")
    if notional is not None:
        return _decimal(notional, "minNotional"), _decimal(notional, "maxNotional")
    if legacy is not None:
        return _decimal(legacy, "minNotional"), None
    raise BinanceMetadataError("missing notional filter")


def normalize_exchange_info(
    exchange_info: Any,
    execution_rules: Any,
    *,
    taker_commission: Decimal,
) -> NormalizedBinanceExchangeInfo:
    if not isinstance(exchange_info, Mapping):
        raise BinanceMetadataError("exchangeInfo must be an object")
    if not isinstance(execution_rules, Mapping):
        raise BinanceMetadataError("executionRules must be an object")
    symbols = _indexed(
        exchange_info.get("symbols"), key="symbol", label="symbol", maximum=MAX_MARKETS
    )
    rules = _indexed(
        execution_rules.get("symbolRules"),
        key="symbol",
        label="symbol rule",
        maximum=MAX_RULES,
    )
    if not taker_commission.is_finite() or not Decimal("0.001") <= taker_commission < 1:
        raise BinanceMetadataError("configured taker commission must be in [0.001, 1)")

    markets: list[MarketRules] = []
    rejections: list[BinanceMetadataRejection] = []
    for raw_symbol, raw in symbols.items():
        if raw.get("status") != "TRADING" or raw.get("isSpotTradingAllowed") is not True:
            continue
        try:
            symbol = _identity(raw, "symbol")
            base = _identity(raw, "baseAsset")
            quote = _identity(raw, "quoteAsset")
            if symbol != f"{base}{quote}":
                raise BinanceMetadataError("symbol does not match baseAsset/quoteAsset")
            order_types = raw.get("orderTypes")
            if not isinstance(order_types, list) or "MARKET" not in order_types:
                raise BinanceMetadataError("MARKET orders are unavailable")
            filters = _indexed(
                raw.get("filters"), key="filterType", label="filter", maximum=MAX_FILTERS
            )
            lot_size = filters.get("LOT_SIZE")
            if lot_size is None:
                raise BinanceMetadataError("missing LOT_SIZE filter")
            step = _decimal(lot_size, "stepSize")
            minimum_quote, maximum_quote = _notional(filters)
            execution_rule = rules.get(symbol)
            if execution_rule is None:
                raise BinanceMetadataError("missing public execution rule")
            markets.append(
                MarketRules(
                    symbol=symbol,
                    base_asset=base,
                    quote_asset=quote,
                    base_asset_precision=_power_of_ten_precision(step),
                    min_base_quantity=_decimal(lot_size, "minQty"),
                    max_base_quantity=_decimal(lot_size, "maxQty"),
                    min_quote_amount=minimum_quote,
                    max_quote_amount=maximum_quote,
                    taker_commission=taker_commission,
                    allowed_sides=frozenset({ConversionSide.BUY, ConversionSide.SELL}),
                    price_protection=_protection(execution_rule),
                    exchange="BINANCE",
                )
            )
        except BinanceMetadataError as error:
            rejections.append(BinanceMetadataRejection(raw_symbol, str(error)))
    return NormalizedBinanceExchangeInfo(
        tuple(sorted(markets, key=lambda market: market.symbol)),
        tuple(sorted(rejections, key=lambda rejection: rejection.symbol)),
    )
