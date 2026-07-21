import httpx
import pytest

from tri_arb.cli import main
from tri_arb.config import Settings
from tri_arb.doctor import Diagnostic, run_diagnostics
from tri_arb.exchange.mexc import MexcRestClient


@pytest.mark.asyncio
async def test_doctor_checks_database_protobuf_and_all_public_rest_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/api/v3/ping": {},
            "/api/v3/time": {"serverTime": 1_100},
            "/api/v3/exchangeInfo": {"symbols": []},
            "/api/v3/ticker/bookTicker": [],
        }
        return httpx.Response(200, json=payloads[request.url.path])

    times = iter([1_000, 1_020, 1_030])
    async with httpx.AsyncClient(
        base_url="https://api.mexc.test",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        rest_client = MexcRestClient(
            "https://unused.test",
            client=http_client,
            retry_delays=(),
            now_ms=lambda: next(times),
        )
        results = await run_diagnostics(
            Settings(database_url="sqlite+aiosqlite:///:memory:", _env_file=None),
            rest_client=rest_client,
        )

    assert [result.name for result in results] == [
        "configuration",
        "database",
        "protobuf",
        "mexc ping",
        "mexc time",
        "mexc exchangeInfo",
        "mexc bookTicker",
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
