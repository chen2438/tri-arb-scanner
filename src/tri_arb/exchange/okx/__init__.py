"""Public OKX spot market-data adapter."""

from tri_arb.exchange.okx.depth import OkxDepthError, OkxOrderBookState
from tri_arb.exchange.okx.market_data import OkxMarketDataService
from tri_arb.exchange.okx.metadata import (
    NormalizedOkxInstruments,
    OkxMetadataRejection,
    normalize_instruments,
)
from tri_arb.exchange.okx.rest import (
    NormalizedOkxTickers,
    OkxRestClient,
    OkxRestError,
    OkxRestProtocolError,
    normalize_price_limit,
    normalize_tickers,
)

__all__ = [
    "NormalizedOkxInstruments",
    "NormalizedOkxTickers",
    "OkxDepthError",
    "OkxMarketDataService",
    "OkxMetadataRejection",
    "OkxOrderBookState",
    "OkxRestClient",
    "OkxRestError",
    "OkxRestProtocolError",
    "normalize_instruments",
    "normalize_price_limit",
    "normalize_tickers",
]
