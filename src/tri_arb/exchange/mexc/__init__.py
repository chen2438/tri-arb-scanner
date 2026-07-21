"""MEXC spot-market adapter."""

from tri_arb.exchange.mexc.depth import (
    DepthTiming,
    MexcDepthDecodeError,
    decode_depth_frame,
    depth_channel,
    validate_depth_timing,
)
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
from tri_arb.exchange.mexc.subscriptions import (
    MarketLease,
    SubscriptionPlan,
    reconcile_subscriptions,
)
from tri_arb.exchange.mexc.websocket import (
    DepthUpdate,
    MexcDepthWebSocketShard,
    WebSocketState,
    WebSocketStatus,
)

__all__ = [
    "BookTickerRejection",
    "DepthTiming",
    "DepthUpdate",
    "MarketLease",
    "MarketMetadataRejection",
    "MexcDepthDecodeError",
    "MexcDepthWebSocketShard",
    "MexcMetadataError",
    "MexcRestClient",
    "MexcRestError",
    "MexcRestProtocolError",
    "NormalizedBookTickers",
    "NormalizedExchangeInfo",
    "ServerClock",
    "SubscriptionPlan",
    "WebSocketState",
    "WebSocketStatus",
    "decode_depth_frame",
    "depth_channel",
    "normalize_book_tickers",
    "normalize_exchange_info",
    "reconcile_subscriptions",
    "validate_depth_timing",
]
