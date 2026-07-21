from fastapi.testclient import TestClient

from tri_arb.api import create_app
from tri_arb.config import Settings
from tri_arb.services import OpportunityHub


class _Runtime:
    def active(self):
        return ()


class _Services:
    def __init__(self) -> None:
        self.hub = OpportunityHub(now_ms=lambda: 1)
        self.scanner_runtime = _Runtime()

    async def status_payload(self):
        return {"phase": "initializing", "ready": False, "last_error": None}

    async def snapshot_message(self):
        return {
            "type": "snapshot",
            "sequence": self.hub.sequence,
            "data": {"opportunities": [], "status": await self.status_payload()},
        }


def test_websocket_sends_complete_snapshot_first() -> None:
    services = _Services()
    app = create_app(
        Settings(_env_file=None),
        services=services,  # type: ignore[arg-type]
        manage_services=False,
    )

    with TestClient(app) as client, client.websocket_connect("/ws/opportunities") as socket:
        message = socket.receive_json()

    assert message["type"] == "snapshot"
    assert message["sequence"] == 0
    assert message["data"]["opportunities"] == []
