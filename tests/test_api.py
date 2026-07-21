import pytest
from httpx import ASGITransport, AsyncClient

from tri_arb.api import create_app
from tri_arb.config import Settings


@pytest.mark.asyncio
async def test_health_and_readiness_are_honest_about_scanner_state() -> None:
    transport = ASGITransport(app=create_app(Settings(_env_file=None), manage_services=False))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        live = await client.get("/api/health/live")
        ready = await client.get("/api/health/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "reason": "market data phase is initializing",
    }


@pytest.mark.asyncio
async def test_config_endpoint_returns_only_public_values() -> None:
    transport = ASGITransport(app=create_app(Settings(_env_file=None), manage_services=False))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["mexc_rest_url"] == "https://api.mexc.com"
    assert response.json()["notional"] == "100"


@pytest.mark.asyncio
async def test_diagnostics_endpoint_is_honest_before_the_first_scan() -> None:
    transport = ASGITransport(app=create_app(Settings(_env_file=None), manage_services=False))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/diagnostics")

    assert response.status_code == 200
    assert response.json() == {"diagnostics": None}
