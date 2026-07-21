"""Strict application configuration with fail-closed network boundaries."""

from __future__ import annotations

import ipaddress
import os
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "TRI_ARB_"
LOOPBACK_NAMES = {"localhost"}
SUPPLEMENTAL_ANCHOR_ASSETS = ("USDC", "USD1")


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    if normalized in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_service_url(value: str, *, secure_scheme: str, local_scheme: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL must have a host and cannot contain credentials, query, or fragment")
    if parsed.scheme == secure_scheme:
        return value.rstrip("/")
    if parsed.scheme == local_scheme and _is_loopback_host(parsed.hostname):
        return value.rstrip("/")
    raise ValueError(f"URL must use {secure_scheme}, except loopback may use {local_scheme}")


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    mexc_rest_url: str = "https://api.mexc.com"
    mexc_ws_url: str = "wss://wbs-api.mexc.com/ws"
    okx_enabled: bool = True
    okx_rest_url: str = "https://www.okx.com"
    okx_ws_url: str = "wss://ws.okx.com:8443/ws/v5/public"
    okx_taker_commission: Decimal = Field(default=Decimal("0.0015"), ge=Decimal("0.0015"), lt=1)
    binance_enabled: bool = True
    binance_rest_url: str = "https://api.binance.com"
    binance_ws_url: str = "wss://data-stream.binance.vision/ws"
    binance_taker_commission: Decimal = Field(
        default=Decimal("0.001"), ge=Decimal("0.001"), lt=1
    )
    bybit_enabled: bool = True
    bybit_rest_url: str = "https://api.bybit.com"
    bybit_ws_url: str = "wss://stream.bybit.com/v5/public/spot"
    bybit_taker_commission: Decimal = Field(
        default=Decimal("0.002"), ge=Decimal("0.002"), lt=1
    )
    anchor_asset: Literal["USDT"] = "USDT"
    notional: Decimal = Field(default=Decimal("100"), gt=0)
    min_net_return_bps: Decimal = Field(default=Decimal("20"), ge=0)
    close_net_return_bps: Decimal = Field(default=Decimal("15"), ge=0)
    safety_buffer_bps: Decimal = Field(default=Decimal("5"), ge=0)
    book_ticker_interval_ms: int = Field(default=1000, ge=250, le=60_000)
    shortlist_routes: int = Field(default=20, ge=1, le=20)
    depth_levels: int = 20
    max_depth_age_ms: int = Field(default=2000, ge=100, le=60_000)
    max_leg_skew_ms: int = Field(default=1000, ge=0, le=60_000)
    history_retention_days: int = Field(default=7, ge=1, le=365)
    database_url: str = "sqlite+aiosqlite:///./tri_arb.db"

    @property
    def anchor_assets(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.anchor_asset, *SUPPLEMENTAL_ANCHOR_ASSETS)))

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not _is_loopback_host(value):
            raise ValueError("v0.1 only permits a loopback bind address")
        return value

    @field_validator("mexc_rest_url", "okx_rest_url", "binance_rest_url", "bybit_rest_url")
    @classmethod
    def validate_rest_url(cls, value: str) -> str:
        return _validate_service_url(value, secure_scheme="https", local_scheme="http")

    @field_validator("mexc_ws_url", "okx_ws_url", "binance_ws_url", "bybit_ws_url")
    @classmethod
    def validate_ws_url(cls, value: str) -> str:
        return _validate_service_url(value, secure_scheme="wss", local_scheme="ws")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("sqlite+aiosqlite://") or "?" in value or "#" in value:
            raise ValueError("v0.1 database must be a plain sqlite+aiosqlite URL")
        if value == "sqlite+aiosqlite://":
            raise ValueError("database URL must include a path or in-memory target")
        return value

    @field_validator("depth_levels")
    @classmethod
    def validate_depth_levels(cls, value: int) -> int:
        if value != 20:
            raise ValueError("v0.1 depth level must be 20")
        return value

    @model_validator(mode="after")
    def validate_thresholds(self) -> Settings:
        if self.close_net_return_bps >= self.min_net_return_bps:
            raise ValueError("close threshold must be lower than the opportunity threshold")
        if self.max_leg_skew_ms > self.max_depth_age_ms:
            raise ValueError("leg skew cannot exceed maximum depth age")
        return self

    def public_dict(self) -> dict[str, object]:
        """Return the safe, JSON-ready configuration exposed by the local API."""

        return {
            "host": self.host,
            "port": self.port,
            "mexc_rest_url": self.mexc_rest_url,
            "mexc_ws_url": self.mexc_ws_url,
            "okx_enabled": self.okx_enabled,
            "okx_rest_url": self.okx_rest_url,
            "okx_ws_url": self.okx_ws_url,
            "okx_taker_commission": str(self.okx_taker_commission),
            "binance_enabled": self.binance_enabled,
            "binance_rest_url": self.binance_rest_url,
            "binance_ws_url": self.binance_ws_url,
            "binance_taker_commission": str(self.binance_taker_commission),
            "bybit_enabled": self.bybit_enabled,
            "bybit_rest_url": self.bybit_rest_url,
            "bybit_ws_url": self.bybit_ws_url,
            "bybit_taker_commission": str(self.bybit_taker_commission),
            "anchor_asset": self.anchor_asset,
            "anchor_assets": list(self.anchor_assets),
            "notional": str(self.notional),
            "min_net_return_bps": str(self.min_net_return_bps),
            "close_net_return_bps": str(self.close_net_return_bps),
            "safety_buffer_bps": str(self.safety_buffer_bps),
            "book_ticker_interval_ms": self.book_ticker_interval_ms,
            "shortlist_routes": self.shortlist_routes,
            "depth_levels": self.depth_levels,
            "max_depth_age_ms": self.max_depth_age_ms,
            "max_leg_skew_ms": self.max_leg_skew_ms,
            "history_retention_days": self.history_retention_days,
            "database_url": self.database_url,
        }


def load_settings() -> Settings:
    """Load settings and reject misspelled project-prefixed environment variables."""

    known = {f"{ENV_PREFIX}{name.upper()}" for name in Settings.model_fields}
    unknown = sorted(
        name for name in os.environ if name.startswith(ENV_PREFIX) and name not in known
    )
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unknown Tri-Arb environment variable(s): {joined}")
    return Settings()
