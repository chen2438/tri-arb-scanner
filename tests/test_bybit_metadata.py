from decimal import Decimal

import pytest

from tri_arb.exchange.bybit import BybitMetadataError, normalize_instruments


def _instrument(**overrides):
    value = {
        "symbol": "BTCUSDT",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "status": "Trading",
        "lotSizeFilter": {
            "basePrecision": "0.000001",
            "minOrderAmt": "5",
            "maxMarketOrderQty": "120",
        },
    }
    value.update(overrides)
    return value


def test_normalizes_spot_market_rules_with_explicit_price_limits() -> None:
    result = normalize_instruments(
        {"category": "spot", "list": [_instrument()]},
        taker_commission=Decimal("0.002"),
    )

    market = result.markets[0]
    assert market.exchange == "BYBIT"
    assert market.base_asset_precision == 6
    assert market.min_base_quantity == Decimal("0.000001")
    assert market.min_quote_amount == Decimal("5")
    assert market.max_base_quantity == Decimal("120")
    assert market.requires_explicit_price_limit is True


def test_quarantines_invalid_market_and_rejects_unsafe_fee() -> None:
    result = normalize_instruments(
        {"category": "spot", "list": [_instrument(lotSizeFilter={})]},
        taker_commission=Decimal("0.002"),
    )
    assert not result.markets
    assert result.rejections[0].reason == "invalid basePrecision"

    with pytest.raises(BybitMetadataError, match=r"\[0.002, 1\)"):
        normalize_instruments(
            {"category": "spot", "list": []},
            taker_commission=Decimal("0.001"),
        )
