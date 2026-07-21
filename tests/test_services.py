import asyncio

import pytest

from tests.test_market_data import FakeRestClient
from tri_arb.config import Settings
from tri_arb.market_data import MarketDataService
from tri_arb.services import ApplicationServices


@pytest.mark.asyncio
async def test_application_services_start_ready_and_stop_cleanly(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'services.db'}",
        mexc_ws_url="ws://127.0.0.1:1/ws",
        _env_file=None,
    )
    market = MarketDataService(
        settings,
        rest_client=FakeRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1_000_000,
    )
    services = ApplicationServices(settings, market_data=market, now_ms=lambda: 1_000_000)

    await services.start()
    for _ in range(20):
        status = await services.status_payload()
        if status["ready"]:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"services did not become ready: {status}")
    await services.stop()

    assert status["market_count"] == 3
    assert status["route_count"] == 2
    assert services.scanner_runtime.status().started is False
