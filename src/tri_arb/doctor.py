"""Read-only local and public-upstream diagnostics for the scanner CLI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tri_arb.config import Settings
from tri_arb.exchange.mexc import MexcRestClient, decode_depth_frame, depth_channel
from tri_arb.exchange.mexc.proto.PushDataV3ApiWrapper_pb2 import PushDataV3ApiWrapper
from tri_arb.exchange.okx import OkxRestClient


@dataclass(frozen=True, slots=True)
class Diagnostic:
    name: str
    ok: bool
    detail: str


def _safe_error(error: Exception) -> str:
    detail = " ".join(str(error).split()) or type(error).__name__
    return detail[:160]


async def _capture[T](
    results: list[Diagnostic],
    name: str,
    operation: Callable[[], Awaitable[T]],
    describe: Callable[[T], str],
) -> None:
    try:
        value = await operation()
    except Exception as error:
        results.append(Diagnostic(name, False, _safe_error(error)))
    else:
        results.append(Diagnostic(name, True, describe(value)))


async def _check_database(database_url: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT sqlite_version()"))
            await connection.execute(text("CREATE TEMP TABLE tri_arb_doctor(value INTEGER)"))
            await connection.execute(text("DROP TABLE tri_arb_doctor"))
            await connection.rollback()
    finally:
        await engine.dispose()
    return f"SQLite {version}; temporary write succeeded"


def _check_protobuf() -> str:
    wrapper = PushDataV3ApiWrapper()
    wrapper.channel = depth_channel("BTCUSDT")
    wrapper.symbol = "BTCUSDT"
    wrapper.sendTime = 1_784_637_652_300
    wrapper.publicLimitDepths.eventType = "spot@public.limit.depth.v3.api.pb"
    wrapper.publicLimitDepths.version = "42"
    wrapper.publicLimitDepths.bids.add(price="100", quantity="1")
    wrapper.publicLimitDepths.asks.add(price="101", quantity="2")
    book = decode_depth_frame(
        wrapper.SerializeToString(),
        received_time_ms=1_784_637_652_400,
    )
    if book.symbol != "BTCUSDT" or book.version != "42":
        raise RuntimeError("decoded depth fixture does not match its source")
    return "public 20-level frame decoded"


async def run_diagnostics(
    settings: Settings,
    *,
    rest_client: MexcRestClient | None = None,
    okx_rest_client: OkxRestClient | None = None,
) -> tuple[Diagnostic, ...]:
    """Run checks without accessing private exchange APIs or changing scanner records."""

    results = [Diagnostic("configuration", True, f"bind {settings.host}:{settings.port}")]
    await _capture(
        results,
        "database",
        lambda: _check_database(settings.database_url),
        lambda detail: detail,
    )
    try:
        results.append(Diagnostic("protobuf", True, _check_protobuf()))
    except Exception as error:
        results.append(Diagnostic("protobuf", False, _safe_error(error)))

    client = rest_client or MexcRestClient(settings.mexc_rest_url)
    owns_client = rest_client is None
    try:
        await _capture(results, "mexc ping", client.ping, lambda _value: "public API reachable")
        await _capture(
            results,
            "mexc time",
            client.calibrate_clock,
            lambda clock: f"offset {clock.offset_ms} ms; round trip {clock.round_trip_ms} ms",
        )
        await _capture(
            results,
            "mexc exchangeInfo",
            client.exchange_info,
            lambda info: f"{len(info.markets)} markets; {len(info.rejections)} rejected",
        )
        await _capture(
            results,
            "mexc bookTicker",
            client.book_tickers,
            lambda tickers: f"{len(tickers.tickers)} tickers; {len(tickers.rejections)} rejected",
        )
    finally:
        if owns_client:
            await client.aclose()

    if settings.okx_enabled:
        okx_client = okx_rest_client or OkxRestClient(
            settings.okx_rest_url,
            taker_commission=settings.okx_taker_commission,
        )
        owns_okx_client = okx_rest_client is None
        try:
            await _capture(
                results,
                "okx time",
                okx_client.calibrate_clock,
                lambda clock: (
                    f"offset {clock.offset_ms} ms; round trip {clock.round_trip_ms} ms"
                ),
            )
            await _capture(
                results,
                "okx instruments",
                okx_client.instruments,
                lambda info: f"{len(info.markets)} markets; {len(info.rejections)} rejected",
            )
            await _capture(
                results,
                "okx tickers",
                okx_client.tickers,
                lambda tickers: (
                    f"{len(tickers.tickers)} tickers; {len(tickers.rejections)} rejected"
                ),
            )
        finally:
            if owns_okx_client:
                await okx_client.aclose()
    return tuple(results)
