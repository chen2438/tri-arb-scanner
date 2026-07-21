"""Strict normalization of public Bybit V5 spot instrument rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import ConversionSide, MarketRules

MAX_MARKETS = 10_000
MAX_IDENTITY_LENGTH = 64
MAX_DECIMAL_LENGTH = 128
PUBLIC_SPOT_FEE_CEILING = Decimal("0.002")


class BybitMetadataError(ValueError):
    """Raised when Bybit metadata is structurally unusable."""


@dataclass(frozen=True, slots=True)
class BybitMetadataRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedBybitInstruments:
    markets: tuple[MarketRules, ...]
    rejections: tuple[BybitMetadataRejection, ...]


def _identity(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_IDENTITY_LENGTH
        or value != value.strip().upper()
    ):
        raise BybitMetadataError(f"invalid {field}")
    return value


def _decimal(raw: Mapping[str, Any], field: str) -> Decimal:
    value = raw.get(field)
    if not isinstance(value, str) or not value or len(value) > MAX_DECIMAL_LENGTH:
        raise BybitMetadataError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise BybitMetadataError(f"invalid {field}") from error
    if (
        not result.is_finite()
        or result <= 0
        or abs(result.adjusted()) > 60
    ):
        raise BybitMetadataError(f"invalid {field}")
    return result


def _power_of_ten_precision(step: Decimal) -> int:
    normalized = step.normalize()
    if normalized.as_tuple().digits != (1,) or normalized.adjusted() > 0:
        raise BybitMetadataError("basePrecision must be a power of ten")
    return max(0, -normalized.as_tuple().exponent)


def normalize_instruments(
    payload: Any,
    *,
    taker_commission: Decimal,
) -> NormalizedBybitInstruments:
    if not isinstance(payload, Mapping):
        raise BybitMetadataError("instruments result must be an object")
    if payload.get("category") != "spot":
        raise BybitMetadataError("instruments category must be spot")
    items = payload.get("list")
    if not isinstance(items, list) or len(items) > MAX_MARKETS:
        raise BybitMetadataError("instruments list must be bounded")
    if (
        not taker_commission.is_finite()
        or not PUBLIC_SPOT_FEE_CEILING <= taker_commission < 1
    ):
        raise BybitMetadataError("configured taker commission must be in [0.002, 1)")

    markets: list[MarketRules] = []
    rejections: list[BybitMetadataRejection] = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            raise BybitMetadataError(f"instrument at index {index} must be an object")
        label = str(raw.get("symbol", f"index {index}"))[:MAX_IDENTITY_LENGTH]
        if raw.get("status") != "Trading":
            continue
        try:
            symbol = _identity(raw, "symbol")
            if symbol in seen:
                raise BybitMetadataError(f"duplicate instrument: {symbol}")
            seen.add(symbol)
            base = _identity(raw, "baseCoin")
            quote = _identity(raw, "quoteCoin")
            if symbol != f"{base}{quote}":
                raise BybitMetadataError("symbol does not match baseCoin/quoteCoin")
            lot = raw.get("lotSizeFilter")
            if not isinstance(lot, Mapping):
                raise BybitMetadataError("missing lotSizeFilter")
            quantum = _decimal(lot, "basePrecision")
            markets.append(
                MarketRules(
                    symbol=symbol,
                    base_asset=base,
                    quote_asset=quote,
                    base_asset_precision=_power_of_ten_precision(quantum),
                    min_base_quantity=quantum,
                    max_base_quantity=_decimal(lot, "maxMarketOrderQty"),
                    min_quote_amount=_decimal(lot, "minOrderAmt"),
                    max_quote_amount=None,
                    taker_commission=taker_commission,
                    allowed_sides=frozenset({ConversionSide.BUY, ConversionSide.SELL}),
                    requires_explicit_price_limit=True,
                    exchange="BYBIT",
                )
            )
        except (BybitMetadataError, ValueError) as error:
            rejections.append(BybitMetadataRejection(label, str(error)))
    return NormalizedBybitInstruments(
        tuple(sorted(markets, key=lambda market: market.symbol)),
        tuple(sorted(rejections, key=lambda rejection: rejection.symbol)),
    )
