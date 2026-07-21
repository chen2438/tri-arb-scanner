from decimal import Decimal

import pytest

from tri_arb.exchange.binance import BinanceMetadataError, normalize_exchange_info


def _symbol(**overrides):
    value = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "isSpotTradingAllowed": True,
        "orderTypes": ["LIMIT", "MARKET"],
        "filters": [
            {
                "filterType": "LOT_SIZE",
                "minQty": "0.00001",
                "maxQty": "9000",
                "stepSize": "0.00001",
            },
            {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "9000000"},
        ],
    }
    value.update(overrides)
    return value


def _rules():
    return {
        "symbolRules": [
            {
                "symbol": "BTCUSDT",
                "rules": [
                    {
                        "ruleType": "PRICE_RANGE",
                        "bidLimitMultUp": "1.15",
                        "bidLimitMultDown": "0.85",
                        "askLimitMultUp": "1.15",
                        "askLimitMultDown": "0.85",
                    }
                ],
            }
        ]
    }


def test_normalizes_spot_rules_and_public_execution_protection() -> None:
    result = normalize_exchange_info(
        {"symbols": [_symbol()]}, _rules(), taker_commission=Decimal("0.001")
    )

    market = result.markets[0]
    assert market.exchange == "BINANCE"
    assert market.base_asset_precision == 5
    assert market.min_base_quantity == Decimal("0.00001")
    assert market.max_base_quantity == Decimal("9000")
    assert market.min_quote_amount == Decimal("5")
    assert market.max_quote_amount == Decimal("9000000")
    assert market.price_protection is not None
    assert market.price_protection.max_buy_deviation == Decimal("0.15")
    assert market.price_protection.max_sell_deviation == Decimal("0.15")


def test_quarantines_missing_rules_and_rejects_duplicate_symbols() -> None:
    missing = normalize_exchange_info(
        {"symbols": [_symbol(filters=[])]}, _rules(), taker_commission=Decimal("0.001")
    )
    assert not missing.markets
    assert missing.rejections[0].reason == "missing LOT_SIZE filter"

    with pytest.raises(BinanceMetadataError, match="duplicate symbol"):
        normalize_exchange_info(
            {"symbols": [_symbol(), _symbol()]},
            _rules(),
            taker_commission=Decimal("0.001"),
        )


def test_rejects_fee_below_public_standard_rate() -> None:
    with pytest.raises(BinanceMetadataError, match=r"\[0.001, 1\)"):
        normalize_exchange_info(
            {"symbols": [_symbol()]}, _rules(), taker_commission=Decimal("0.0009")
        )
