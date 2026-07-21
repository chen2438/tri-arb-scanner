from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_market_data import FakeRestClient
from tests.test_storage import _confirmed, _cycle
from tri_arb.api import create_app
from tri_arb.config import Settings
from tri_arb.market_data import MarketDataService
from tri_arb.scanner.runtime import ScannerRuntime
from tri_arb.services import ApplicationServices


@pytest.mark.asyncio
async def test_read_only_opportunity_detail_history_filters_and_cursors(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"
    settings = Settings(
        database_url=database_url,
        mexc_ws_url="ws://127.0.0.1:1/ws",
        _env_file=None,
    )
    market = MarketDataService(
        settings,
        rest_client=FakeRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 5_000,
    )
    await market.refresh_metadata()
    await market.calibrate_clock()
    await market.refresh_tickers()
    runtime = ScannerRuntime(settings, now_ms=lambda: 500)
    await runtime.start()
    accepted = _confirmed("510")
    invalid = replace(
        accepted,
        simulation=None,
        confirmed_capacity_usdt=None,
        timing=None,
        reject_reasons=("stale_depth",),
    )
    await runtime.process_cycle(_cycle(1_000, accepted))
    await runtime.process_cycle(_cycle(2_000, invalid))
    await runtime.process_cycle(_cycle(3_000, accepted))
    await runtime.process_cycle(_cycle(4_000, invalid))
    await runtime.process_cycle(_cycle(5_000, accepted))
    services = ApplicationServices(
        settings,
        market_data=market,
        scanner_runtime=runtime,
        now_ms=lambda: 5_100,
    )
    app = create_app(settings, services=services, manage_services=False)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get("/api/health/ready")
            status = await client.get("/api/status")
            active = await client.get("/api/opportunities")
            active_usdc = await client.get("/api/opportunities", params={"anchor": "USDC"})
            active_mexc = await client.get("/api/opportunities", params={"exchange": "MEXC"})
            active_okx = await client.get("/api/opportunities", params={"exchange": "OKX"})
            active_item = active.json()["items"][0]
            detail = await client.get(f"/api/opportunities/{active_item['id']}")
            first_history = await client.get("/api/history", params={"limit": 1})
            first_page = first_history.json()
            second_history = await client.get(
                "/api/history",
                params={"limit": 1, "cursor": first_page["next_cursor"]},
            )
            filtered = await client.get(
                "/api/history",
                params={"from": "1970-01-01T00:00:03Z"},
            )
            history_usdc = await client.get("/api/history", params={"anchor": "USDC"})
            history_mexc = await client.get("/api/history", params={"exchange": "MEXC"})
            history_okx = await client.get("/api/history", params={"exchange": "OKX"})
            unknown = await client.get("/api/opportunities/00000000-0000-0000-0000-000000000000")
            invalid_cursor = await client.get("/api/history", params={"cursor": "bad"})
            invalid_limit = await client.get("/api/opportunities", params={"limit": 0})

        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}
        assert status.json()["market_count"] == 3
        assert status.json()["active_opportunity_count"] == 1
        assert active.status_code == 200
        assert active_usdc.json()["items"] == []
        assert len(active_mexc.json()["items"]) == 1
        assert active_okx.json()["items"] == []
        assert active_item["state"] == "active"
        assert isinstance(active_item["net_return_bps"], str)
        assert detail.json() == active_item
        assert len(first_page["items"]) == 1
        assert first_page["next_cursor"] is not None
        assert len(second_history.json()["items"]) == 1
        assert second_history.json()["next_cursor"] is None
        assert len(filtered.json()["items"]) == 1
        assert history_usdc.json()["items"] == []
        assert len(history_mexc.json()["items"]) == 2
        assert history_okx.json()["items"] == []
        assert unknown.status_code == 404
        assert invalid_cursor.status_code == 422
        assert invalid_limit.status_code == 422
    finally:
        await runtime.stop()
