import pytest

from tests.test_market_data import FakeRestClient
from tri_arb.config import Settings
from tri_arb.market_data import MarketDataService
from tri_arb.scanner import ScannerEngine


@pytest.mark.asyncio
async def test_cycle_updates_shortlist_without_publishing_unconfirmed_routes() -> None:
    settings = Settings(mexc_ws_url="ws://127.0.0.1:1/ws", _env_file=None)
    market_data = MarketDataService(
        settings,
        rest_client=FakeRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1_000,
    )
    await market_data.refresh_metadata()
    await market_data.calibrate_clock()
    await market_data.refresh_tickers()
    engine = ScannerEngine(settings, now_ms=lambda: 1_000)

    first = await engine.cycle(market_data)
    await market_data.reconcile_depth()
    second = await engine.cycle(market_data)

    assert len(first.broad_candidates) == 2
    assert first.confirmed == ()
    assert {outcome.reject_reasons for outcome in first.confirmations} == {("not_selected",)}
    assert second.confirmed == ()
    assert {outcome.reject_reasons for outcome in second.confirmations} == {
        ("missing_current_depth",)
    }
