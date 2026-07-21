import json
from pathlib import Path

import httpx
import pytest

from tri_arb.exchange.mexc import MexcRestClient

FIXTURE = Path(__file__).parent / "fixtures" / "mexc_public_rest_sample.json"


@pytest.mark.asyncio
async def test_recorded_public_rest_sample_matches_adapter_contract() -> None:
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payloads = {
        "/api/v3/ping": sample["ping"],
        "/api/v3/time": sample["time"],
        "/api/v3/exchangeInfo": sample["exchangeInfo"],
        "/api/v3/ticker/bookTicker": sample["bookTicker"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payloads[request.url.path])

    transport = httpx.MockTransport(handler)
    times = iter([1_784_638_199_990, 1_784_638_200_010, 1_784_638_200_020])
    async with httpx.AsyncClient(base_url="https://api.mexc.test", transport=transport) as http:
        client = MexcRestClient(
            "https://unused.test",
            client=http,
            retry_delays=(),
            now_ms=lambda: next(times),
        )
        await client.ping()
        clock = await client.calibrate_clock()
        metadata = await client.exchange_info()
        tickers = await client.book_tickers()

    assert clock.offset_ms == 0
    assert len(metadata.markets) == 3
    assert len(tickers.tickers) == 3
    assert metadata.rejections == ()
    assert tickers.rejections == ()
