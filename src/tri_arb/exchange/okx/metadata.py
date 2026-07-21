"""Strict normalization of public OKX spot instrument rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from tri_arb.domain.models import ConversionSide, MarketRules

MAX_MARKETS = 10_000
MAX_IDENTITY_LENGTH = 64
MAX_DECIMAL_LENGTH = 128


@dataclass(frozen=True, slots=True)
class OkxMetadataRejection:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedOkxInstruments:
    markets: tuple[MarketRules, ...]
    rejections: tuple[OkxMetadataRejection, ...]


def _identity(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_IDENTITY_LENGTH
        or value != value.strip().upper()
    ):
        raise ValueError(f"invalid {field}")
    return value


def _positive_decimal(raw: Mapping[str, Any], field: str) -> Decimal:
    value = raw.get(field)
    if not isinstance(value, str) or not value or len(value) > MAX_DECIMAL_LENGTH:
        raise ValueError(f"invalid {field}")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid {field}") from error
    if not result.is_finite() or result <= 0 or abs(result.adjusted()) > 60:
        raise ValueError(f"invalid {field}")
    return result


def _base_precision(lot_size: Decimal) -> int:
    normalized = lot_size.normalize()
    if normalized.as_tuple().digits != (1,) or normalized.adjusted() > 0:
        raise ValueError("lotSz must be a power-of-ten base quantity step")
    return max(0, -normalized.as_tuple().exponent)


def normalize_instruments(
    payload: Any,
    *,
    taker_commission: Decimal,
) -> NormalizedOkxInstruments:
    if not isinstance(payload, list) or len(payload) > MAX_MARKETS:
        raise ValueError("OKX instruments response must be a bounded list")
    if not taker_commission.is_finite() or not Decimal(0) <= taker_commission < Decimal(1):
        raise ValueError("OKX configured taker commission must be in [0, 1)")
    markets: list[MarketRules] = []
    rejections: list[OkxMetadataRejection] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, Mapping):
            raise ValueError(f"OKX instrument at index {index} must be an object")
        raw_symbol = raw.get("instId")
        if isinstance(raw_symbol, str) and len(raw_symbol) <= MAX_IDENTITY_LENGTH:
            if raw_symbol in seen:
                raise ValueError(f"duplicate OKX instrument: {raw_symbol}")
            seen.add(raw_symbol)
        if raw.get("instType") != "SPOT" or raw.get("state") != "live":
            continue
        try:
            symbol = _identity(raw, "instId")
            base = _identity(raw, "baseCcy")
            quote = _identity(raw, "quoteCcy")
            if symbol != f"{base}-{quote}":
                raise ValueError("instId does not match baseCcy/quoteCcy")
            lot_size = _positive_decimal(raw, "lotSz")
            market = MarketRules(
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                base_asset_precision=_base_precision(lot_size),
                min_base_quantity=_positive_decimal(raw, "minSz"),
                min_quote_amount=None,
                max_quote_amount=None,
                taker_commission=taker_commission,
                allowed_sides=frozenset({ConversionSide.BUY, ConversionSide.SELL}),
                exchange="OKX",
                max_base_quantity=None,
                requires_explicit_price_limit=True,
            )
        except ValueError as error:
            label = str(raw_symbol or f"index {index}")[:MAX_IDENTITY_LENGTH]
            rejections.append(OkxMetadataRejection(label, str(error)))
            continue
        markets.append(market)
    return NormalizedOkxInstruments(
        tuple(sorted(markets, key=lambda market: market.symbol)),
        tuple(sorted(rejections, key=lambda rejection: rejection.symbol)),
    )
