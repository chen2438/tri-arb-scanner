from decimal import Decimal

import pytest

from tri_arb.domain.models import ConversionSide
from tri_arb.exchange.mexc import MexcMetadataError, normalize_exchange_info


def _symbol(**overrides):
    payload = {
        "symbol": "BTCUSDT",
        "status": "1",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "baseAssetPrecision": 8,
        "baseSizePrecision": "0.00001",
        "quoteAmountPrecision": "1",
        "maxQuoteAmount": "1000000",
        "takerCommission": "0.0005",
        "isSpotTradingAllowed": True,
        "tradeSideType": 1,
    }
    payload.update(overrides)
    return payload


def test_normalizes_enabled_market_rules_without_binary_floats() -> None:
    result = normalize_exchange_info({"symbols": [_symbol()]})
    (market,) = result.markets

    assert result.rejections == ()
    assert market.symbol == "BTCUSDT"
    assert market.min_base_quantity == Decimal("0.00001")
    assert market.taker_commission == Decimal("0.0005")
    assert market.allowed_sides == frozenset({ConversionSide.BUY, ConversionSide.SELL})


@pytest.mark.parametrize(
    ("base_asset_precision", "expected_minimum"),
    [(8, Decimal("0.00000001")), (2, Decimal("0.01")), (0, Decimal("1"))],
)
def test_uses_quantity_quantum_when_mexc_publishes_zero_minimum(
    base_asset_precision: int, expected_minimum: Decimal
) -> None:
    result = normalize_exchange_info(
        {
            "symbols": [
                _symbol(
                    baseAssetPrecision=base_asset_precision,
                    baseSizePrecision="0",
                )
            ]
        }
    )
    (market,) = result.markets

    assert result.rejections == ()
    assert market.min_base_quantity == expected_minimum


@pytest.mark.parametrize(
    ("side_type", "expected"),
    [
        (2, frozenset({ConversionSide.BUY})),
        (3, frozenset({ConversionSide.SELL})),
    ],
)
def test_respects_one_sided_trading(side_type: int, expected) -> None:
    result = normalize_exchange_info({"symbols": [_symbol(tradeSideType=side_type)]})
    (market,) = result.markets

    assert market.allowed_sides == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "2"},
        {"status": "3"},
        {"isSpotTradingAllowed": False},
        {"tradeSideType": 4},
    ],
)
def test_ignores_known_unavailable_markets(overrides) -> None:
    result = normalize_exchange_info({"symbols": [_symbol(**overrides)]})

    assert result.markets == ()
    assert result.rejections == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": "mystery"}, "unknown status"),
        ({"tradeSideType": 9}, "unknown tradeSideType"),
        ({"takerCommission": "NaN"}, "invalid takerCommission"),
        ({"baseSizePrecision": "-0.01"}, "minimum order rules"),
        ({"baseAssetPrecision": 8.5}, "invalid baseAssetPrecision"),
        ({"baseAssetPrecision": 31}, "precision must be between"),
        ({"status": True}, "unknown status"),
        ({"symbol": "btcusdt"}, "invalid symbol"),
    ],
)
def test_rejects_unknown_or_invalid_enabled_market_rules(overrides, message: str) -> None:
    result = normalize_exchange_info({"symbols": [_symbol(**overrides)]})

    assert result.markets == ()
    assert len(result.rejections) == 1
    assert message in result.rejections[0].reason


def test_rejects_duplicate_symbols() -> None:
    with pytest.raises(MexcMetadataError, match="duplicate symbol"):
        normalize_exchange_info({"symbols": [_symbol(), _symbol()]})
