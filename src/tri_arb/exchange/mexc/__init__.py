"""MEXC spot-market adapter."""

from tri_arb.exchange.mexc.metadata import (
    MarketMetadataRejection,
    MexcMetadataError,
    NormalizedExchangeInfo,
    normalize_exchange_info,
)

__all__ = [
    "MarketMetadataRejection",
    "MexcMetadataError",
    "NormalizedExchangeInfo",
    "normalize_exchange_info",
]
