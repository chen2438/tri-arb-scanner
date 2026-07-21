import asyncio

import pytest

import tri_arb.services as services_module
from tests.test_market_data import FakeRestClient
from tri_arb.config import Settings
from tri_arb.market_data import MarketDataService
from tri_arb.services import ApplicationServices


@pytest.mark.asyncio
async def test_application_services_start_core_coverage_and_stop_cleanly(tmp_path) -> None:
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
        if status["market_count"] == 3 and services.scanner_runtime.status().started:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"services did not initialize: {status}")
    await services.stop()

    assert status["market_count"] == 3
    assert status["route_count"] == 2
    assert status["core_market_count"] == 3
    assert status["ready"] is False
    assert services.scanner_runtime.status().started is False


@pytest.mark.asyncio
async def test_stop_cancels_background_task_after_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services_module, "SHUTDOWN_GRACE_SECONDS", 0.01)
    settings = Settings(_env_file=None)
    services = ApplicationServices(settings, market_data=object())  # type: ignore[arg-type]
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def blocked_retry_after() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        finally:
            finalized.set()

    task = asyncio.create_task(blocked_retry_after())
    services._tasks = (task,)
    await started.wait()

    await asyncio.wait_for(services.stop(), timeout=0.2)

    assert task.cancelled()
    assert finalized.is_set()
    assert services._tasks == ()
