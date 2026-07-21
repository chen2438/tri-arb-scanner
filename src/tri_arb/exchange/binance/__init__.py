"""Public Binance spot market-data adapter."""

from tri_arb.exchange.binance.depth import (
    BinanceDepthError,
    BinanceDepthEvent,
    BinanceDepthSnapshot,
    BinanceOrderBookState,
    normalize_depth_event,
    normalize_depth_snapshot,
)
from tri_arb.exchange.binance.metadata import (
    BinanceMetadataError,
    BinanceMetadataRejection,
    NormalizedBinanceExchangeInfo,
    normalize_exchange_info,
)
from tri_arb.exchange.binance.rest import (
    BinanceRestClient,
    BinanceRestError,
    BinanceRestProtocolError,
    NormalizedBinanceTickers,
    normalize_tickers,
)
from tri_arb.exchange.binance.websocket import BinanceDepthWebSocketShard

__all__ = [
    "BinanceDepthError",
    "BinanceDepthEvent",
    "BinanceDepthSnapshot",
    "BinanceDepthWebSocketShard",
    "BinanceMetadataError",
    "BinanceMetadataRejection",
    "BinanceOrderBookState",
    "BinanceRestClient",
    "BinanceRestError",
    "BinanceRestProtocolError",
    "NormalizedBinanceExchangeInfo",
    "NormalizedBinanceTickers",
    "normalize_depth_event",
    "normalize_depth_snapshot",
    "normalize_exchange_info",
    "normalize_tickers",
]
