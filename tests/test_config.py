from decimal import Decimal

import pytest
from pydantic import ValidationError

from tri_arb.config import Settings, load_settings


def test_defaults_match_documented_safe_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.anchor_asset == "USDT"
    assert settings.notional == Decimal("100")
    assert settings.min_net_return_bps == Decimal("20")
    assert settings.depth_levels == 20


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "scanner.example"])
def test_rejects_non_loopback_bind_addresses(host: str) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(host=host, _env_file=None)


@pytest.mark.parametrize(
    "database_url",
    ["postgresql+asyncpg://localhost/tri_arb", "sqlite+aiosqlite:///db.sqlite?mode=ro"],
)
def test_rejects_unsupported_or_parameterized_database_urls(database_url: str) -> None:
    with pytest.raises(ValidationError, match="database"):
        Settings(database_url=database_url, _env_file=None)


def test_allows_insecure_upstream_only_for_loopback() -> None:
    local = Settings(
        mexc_rest_url="http://127.0.0.1:9000/",
        mexc_ws_url="ws://localhost:9001/ws/",
        _env_file=None,
    )

    assert local.mexc_rest_url == "http://127.0.0.1:9000"
    assert local.mexc_ws_url == "ws://localhost:9001/ws"
    with pytest.raises(ValidationError, match="must use https"):
        Settings(mexc_rest_url="http://api.mexc.com", _env_file=None)
    with pytest.raises(ValidationError, match="must use wss"):
        Settings(mexc_ws_url="ws://wbs-api.mexc.com/ws", _env_file=None)


def test_rejects_invalid_threshold_relationships() -> None:
    with pytest.raises(ValidationError, match="close threshold"):
        Settings(min_net_return_bps="20", close_net_return_bps="20", _env_file=None)
    with pytest.raises(ValidationError, match="leg skew"):
        Settings(max_depth_age_ms=1000, max_leg_skew_ms=1001, _env_file=None)


def test_rejects_unknown_prefixed_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRI_ARB_MEXC_REST_URl", "https://typo.example")

    with pytest.raises(ValueError, match="TRI_ARB_MEXC_REST_URl"):
        load_settings()


def test_rejects_unknown_dotenv_variable(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TRI_ARB_UNKNOWN_OPTION=true\n", encoding="utf-8")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=env_file)

    assert error.value.errors()[0]["loc"] == ("tri_arb_unknown_option",)


def test_depth_level_is_fixed_for_v0_1() -> None:
    with pytest.raises(ValidationError, match="depth level must be 20"):
        Settings(depth_levels=10, _env_file=None)


def test_public_configuration_preserves_decimals_as_strings() -> None:
    payload = Settings(_env_file=None).public_dict()

    assert payload["notional"] == "100"
    assert payload["safety_buffer_bps"] == "5"
