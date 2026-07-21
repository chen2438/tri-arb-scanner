import asyncio
import base64
import json
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from tri_arb.exchange.mexc import DepthUpdate, MexcDepthWebSocketShard, WebSocketState
from tri_arb.exchange.mexc.websocket import validate_control_response

FIXTURE = Path(__file__).parent / "fixtures" / "mexc_btcusdt_depth_20.b64"


@pytest.mark.parametrize(
    "message",
    [
        "not-json",
        "[]",
        '{"id":0,"code":1,"msg":"rejected"}',
        '{"id":0,"code":0,"msg":""}',
    ],
)
def test_rejects_invalid_or_failed_websocket_control_responses(message: str) -> None:
    with pytest.raises(ValueError):
        validate_control_response(message)


def test_enforces_single_connection_subscription_limit() -> None:
    async def on_depth(_update: DepthUpdate) -> None:
        return None

    shard = MexcDepthWebSocketShard("ws://localhost/ws", 0, on_depth)

    with pytest.raises(ValueError, match="30"):
        shard.set_symbols(tuple(f"ASSET{index}USDT" for index in range(31)))


@pytest.mark.asyncio
async def test_connects_subscribes_and_delivers_recorded_binary_depth() -> None:
    frame = base64.b64decode(FIXTURE.read_text(encoding="ascii"))
    stop = asyncio.Event()
    updates = []
    statuses = []

    async def handler(websocket) -> None:
        request = json.loads(await websocket.recv())
        assert request == {
            "method": "SUBSCRIPTION",
            "params": ["spot@public.limit.depth.v3.api.pb@BTCUSDT@20"],
        }
        await websocket.send('{"id":0,"code":0,"msg":"subscribed"}')
        await websocket.send(frame)
        await stop.wait()

    async def on_depth(update: DepthUpdate) -> None:
        updates.append(update)
        stop.set()

    async def on_status(status) -> None:
        statuses.append(status)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        shard = MexcDepthWebSocketShard(
            f"ws://127.0.0.1:{port}",
            0,
            on_depth,
            on_status=on_status,
            now_ms=lambda: 1_784_637_652_400,
        )
        shard.set_symbols(("BTCUSDT",))
        await asyncio.wait_for(shard.run(stop), timeout=3)

    assert updates[0].book.symbol == "BTCUSDT"
    assert updates[0].connection_generation == 1
    assert updates[0].subscription_generation == 1
    assert WebSocketState.CONNECTED in {status.state for status in statuses}
    assert statuses[-1].state is WebSocketState.STOPPED


@pytest.mark.asyncio
async def test_publishes_connected_status_after_dynamic_subscription_change() -> None:
    stop = asyncio.Event()
    initially_connected = asyncio.Event()
    updated = asyncio.Event()
    statuses = []

    async def handler(websocket) -> None:
        while not stop.is_set():
            await websocket.recv()
            await websocket.send('{"id":0,"code":0,"msg":"updated"}')

    async def on_depth(_update: DepthUpdate) -> None:
        return None

    async def on_status(status) -> None:
        statuses.append(status)
        if status.state is WebSocketState.CONNECTED:
            if status.subscriptions == ("BTCUSDT",):
                initially_connected.set()
            elif status.subscriptions == ("BTCUSDT", "ETHUSDT"):
                updated.set()
                stop.set()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        shard = MexcDepthWebSocketShard(
            f"ws://127.0.0.1:{port}",
            0,
            on_depth,
            on_status=on_status,
        )
        shard.set_symbols(("BTCUSDT",))
        task = asyncio.create_task(shard.run(stop))
        await asyncio.wait_for(initially_connected.wait(), timeout=3)
        shard.set_symbols(("BTCUSDT", "ETHUSDT"))
        await asyncio.wait_for(updated.wait(), timeout=3)
        await asyncio.wait_for(task, timeout=3)

    connected = [status for status in statuses if status.state is WebSocketState.CONNECTED]
    assert connected[-1].subscriptions == ("BTCUSDT", "ETHUSDT")
