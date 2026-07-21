"""MEXC spot-market adapter."""

from tri_arb.exchange.mexc.metadata import (
    MarketMetadataRejection,
    MexcMetadataError,
    NormalizedExchangeInfo,
    normalize_exchange_info,
)
from tri_arb.exchange.mexc.rest import (
    BookTickerRejection,
    MexcRestClient,
    MexcRestError,
    MexcRestProtocolError,
    NormalizedBookTickers,
    ServerClock,
    normalize_book_tickers,
)

__all__ = [
    "BookTickerRejection",
    "MarketMetadataRejection",
    "MexcMetadataError",
    "MexcRestClient",
    "MexcRestError",
    "MexcRestProtocolError",
    "NormalizedBookTickers",
    "NormalizedExchangeInfo",
    "ServerClock",
    "normalize_book_tickers",
    "normalize_exchange_info",
]
