from decimal import Decimal

import pytest

from tri_arb.exchange.okx import normalize_instruments


def _instrument(**overrides):
    value = {
        "instType": "SPOT",
        "instId": "BTC-USDT",
        "baseCcy": "BTC",
        "quoteCcy": "USDT",
        "state": "live",
        "lotSz": "0.00000001",
        "minSz": "0.00001",
    }
    value.update(overrides)
    return value


def test_normalizes_public_spot_quantity_rules_without_inventing_quote_limits() -> None:
    result = normalize_instruments([_instrument()], taker_commission=Decimal("0.001"))
    (market,) = result.markets

    assert market.exchange == "OKX"
    assert market.symbol == "BTC-USDT"
    assert market.base_asset_precision == 8
    assert market.min_base_quantity == Decimal("0.00001")
    assert market.min_quote_amount is None
    assert market.max_quote_amount is None
    assert market.taker_commission == Decimal("0.001")
    assert market.requires_explicit_price_limit


def test_skips_non_live_instruments_and_quarantines_invalid_rules() -> None:
    result = normalize_instruments(
        [
            _instrument(state="suspend"),
            _instrument(instId="ETH-USDT", baseCcy="ETH", lotSz="0.0003"),
        ],
        taker_commission=Decimal("0.001"),
    )

    assert result.markets == ()
    assert result.rejections[0].symbol == "ETH-USDT"
    assert "power-of-ten" in result.rejections[0].reason


def test_rejects_duplicate_instrument_identity() -> None:
    with pytest.raises(ValueError, match="duplicate OKX instrument"):
        normalize_instruments([_instrument(), _instrument()], taker_commission=Decimal("0.001"))
