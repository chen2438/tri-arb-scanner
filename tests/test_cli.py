import httpx
import pytest

from tri_arb.cli import main
from tri_arb.config import Settings
from tri_arb.doctor import Diagnostic, run_diagnostics
from tri_arb.exchange.binance import BinanceRestClient
from tri_arb.exchange.bybit import BybitRestClient
from tri_arb.exchange.mexc import MexcRestClient
from tri_arb.exchange.okx import OkxRestClient


@pytest.mark.asyncio
async def test_doctor_checks_database_protobuf_and_all_public_rest_endpoints() -> None:
    def mexc_handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/api/v3/ping": {},
            "/api/v3/time": {"serverTime": 1_100},
            "/api/v3/exchangeInfo": {"symbols": []},
            "/api/v3/ticker/bookTicker": [],
        }
        return httpx.Response(200, json=payloads[request.url.path])

    def okx_handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/api/v5/public/time": [{"ts": "1100"}],
            "/api/v5/public/instruments": [],
            "/api/v5/market/tickers": [],
        }
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": payloads[request.url.path]},
        )

    def binance_handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/api/v3/time": {"serverTime": 1100},
            "/api/v3/exchangeInfo": {"symbols": []},
            "/api/v3/executionRules": {"symbolRules": []},
            "/api/v3/ticker/bookTicker": [],
            "/api/v3/ticker/24hr": [],
        }
        return httpx.Response(200, json=payloads[request.url.path])

    def bybit_handler(request: httpx.Request) -> httpx.Response:
        results = {
            "/v5/market/time": {"timeSecond": "1"},
            "/v5/market/instruments-info": {"category": "spot", "list": []},
            "/v5/market/tickers": {"category": "spot", "list": []},
        }
        return httpx.Response(
            200,
            json={"retCode": 0, "retMsg": "OK", "result": results[request.url.path]},
        )

    times = iter([1_000, 1_020, 1_030])
    async with httpx.AsyncClient(
        base_url="https://api.mexc.test",
        transport=httpx.MockTransport(mexc_handler),
    ) as http_client, httpx.AsyncClient(
        base_url="https://www.okx.test",
        transport=httpx.MockTransport(okx_handler),
    ) as okx_http_client:
        binance_http_client = httpx.AsyncClient(
            base_url="https://api.binance.test",
            transport=httpx.MockTransport(binance_handler),
        )
        bybit_http_client = httpx.AsyncClient(
            base_url="https://api.bybit.test",
            transport=httpx.MockTransport(bybit_handler),
        )
        rest_client = MexcRestClient(
            "https://unused.test",
            client=http_client,
            retry_delays=(),
            now_ms=lambda: next(times),
        )
        okx_times = iter([1_000, 1_020, 1_030])
        okx_rest_client = OkxRestClient(
            "https://unused.test",
            taker_commission=Settings(_env_file=None).okx_taker_commission,
            client=okx_http_client,
            retry_delays=(),
            now_ms=lambda: next(okx_times),
        )
        try:
            binance_times = iter([1_000, 1_020, 1_030])
            binance_rest_client = BinanceRestClient(
                "https://unused.test",
                taker_commission=Settings(_env_file=None).binance_taker_commission,
                client=binance_http_client,
                retry_delays=(),
                now_ms=lambda: next(binance_times),
            )
            bybit_times = iter([1_000, 1_020, 1_030])
            bybit_rest_client = BybitRestClient(
                "https://unused.test",
                taker_commission=Settings(_env_file=None).bybit_taker_commission,
                client=bybit_http_client,
                retry_delays=(),
                now_ms=lambda: next(bybit_times),
            )
            results = await run_diagnostics(
                Settings(database_url="sqlite+aiosqlite:///:memory:", _env_file=None),
                rest_client=rest_client,
                okx_rest_client=okx_rest_client,
                binance_rest_client=binance_rest_client,
                bybit_rest_client=bybit_rest_client,
            )
        finally:
            await binance_http_client.aclose()
            await bybit_http_client.aclose()

    assert [result.name for result in results] == [
        "configuration",
        "database",
        "protobuf",
        "mexc ping",
        "mexc time",
        "mexc exchangeInfo",
        "mexc bookTicker",
        "okx time",
        "okx instruments",
        "okx tickers",
        "binance time",
        "binance exchangeInfo",
        "binance tickers",
        "bybit time",
        "bybit instruments",
        "bybit tickers",
    ]
    assert all(result.ok for result in results)


def test_doctor_cli_returns_failure_if_any_check_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def failed(_settings: Settings) -> tuple[Diagnostic, ...]:
        return (
            Diagnostic("configuration", True, "bind 127.0.0.1:8000"),
            Diagnostic("mexc ping", False, "timed out"),
        )

    monkeypatch.setattr("tri_arb.cli.run_diagnostics", failed)
    assert main(["doctor"]) == 1
    output = capsys.readouterr().out
    assert "[ok] configuration" in output
    assert "[fail] mexc ping: timed out" in output
