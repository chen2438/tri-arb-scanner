import pytest

from tests.test_market_data import FakeRestClient
from tests.test_okx_market_data import FakeOkxRestClient
from tri_arb.config import Settings
from tri_arb.exchange.okx.market_data import OkxMarketDataService
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


@pytest.mark.asyncio
async def test_cycle_excludes_expired_rest_tickers_from_broad_screen() -> None:
    settings = Settings(mexc_ws_url="ws://127.0.0.1:1/ws", _env_file=None)
    market_data = MarketDataService(
        settings,
        rest_client=FakeRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1_000,
    )
    await market_data.refresh_metadata()
    await market_data.refresh_tickers()

    cycle = ScannerEngine(settings, now_ms=lambda: 6_001).evaluate(
        await market_data.snapshot()
    )

    assert cycle.broad_candidates == ()
    assert cycle.broad_screen is not None
    assert cycle.broad_screen.priced_route_count == 0


@pytest.mark.asyncio
async def test_multi_exchange_cycle_keeps_venue_shortlists_and_combines_diagnostics() -> None:
    settings = Settings(mexc_ws_url="ws://127.0.0.1:1/ws", _env_file=None)
    mexc = MarketDataService(
        settings,
        rest_client=FakeRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1_000,
    )
    okx = OkxMarketDataService(
        settings,
        rest_client=FakeOkxRestClient(),  # type: ignore[arg-type]
        now_ms=lambda: 1_000,
    )
    for source in (mexc, okx):
        await source.refresh_metadata()
        await source.calibrate_clock()
        await source.refresh_tickers()
    engine = ScannerEngine(settings, now_ms=lambda: 1_000)

    cycle = await engine.cycle_many((mexc, okx))

    assert len(cycle.broad_candidates) == 4
    assert {candidate.route.exchange for candidate in cycle.broad_candidates} == {"MEXC", "OKX"}
    assert cycle.broad_screen is not None
    assert cycle.broad_screen.total_route_count == 4
    assert len(cycle.confirmations) == 4
