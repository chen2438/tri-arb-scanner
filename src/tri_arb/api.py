"""Local-only FastAPI application, read-only REST, and opportunity WebSocket."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from tri_arb import __version__
from tri_arb.config import Settings, load_settings
from tri_arb.presentation import audit_snapshot_to_public, opportunity_to_public
from tri_arb.services import ApplicationServices


def _cursor_encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str | None, expected_kind: str) -> dict[str, Any] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 1_024:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"})
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("kind") != expected_kind:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
    return payload


def _datetime_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(status_code=422, detail={"code": "timezone_required"})
    try:
        return int(value.astimezone(UTC).timestamp() * 1_000)
    except (OSError, OverflowError, ValueError):
        raise HTTPException(status_code=422, detail={"code": "invalid_time"}) from None


def create_app(
    settings: Settings | None = None,
    *,
    services: ApplicationServices | None = None,
    manage_services: bool = True,
) -> FastAPI:
    resolved = settings or load_settings()
    resolved_services = services or ApplicationServices(resolved)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if manage_services:
            await resolved_services.start()
        try:
            yield
        finally:
            if manage_services:
                await resolved_services.stop()

    app = FastAPI(title="Tri-Arb Scanner", version=__version__, lifespan=lifespan)
    app.state.settings = resolved
    app.state.services = resolved_services

    @app.get("/api/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "service": "tri-arb-scanner", "version": __version__}

    @app.get("/api/health/ready")
    async def health_ready() -> JSONResponse:
        status = await resolved_services.status_payload()
        if status["ready"]:
            return JSONResponse(content={"status": "ready"})
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": status["last_error"] or f"market data phase is {status['phase']}",
            },
        )

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return await resolved_services.status_payload()

    @app.get("/api/diagnostics")
    async def diagnostics() -> dict[str, Any]:
        status_payload = await resolved_services.status_payload()
        return {"diagnostics": status_payload["diagnostics"]}

    @app.get("/api/config")
    async def public_config() -> dict[str, str | int]:
        return resolved.public_dict()

    @app.get("/api/opportunities")
    async def opportunities(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        decoded = _cursor_decode(cursor, "active")
        active = resolved_services.scanner_runtime.active()
        if decoded is not None:
            try:
                net_return = Decimal(decoded["net_return_bps"])
                if not net_return.is_finite():
                    raise ValueError
                cursor_key = (
                    -net_return,
                    -int(decoded["last_confirmed_ms"]),
                    str(decoded["id"]),
                )
            except (KeyError, ValueError, TypeError):
                raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
            active = tuple(
                lifecycle
                for lifecycle in active
                if (
                    -lifecycle.current_simulation.net_return_bps,
                    -lifecycle.last_confirmed_ms,
                    lifecycle.lifecycle_id,
                )
                > cursor_key
            )
        page = active[: limit + 1]
        items = page[:limit]
        next_cursor = None
        if len(page) > limit:
            last = items[-1]
            next_cursor = _cursor_encode(
                {
                    "kind": "active",
                    "net_return_bps": str(last.current_simulation.net_return_bps),
                    "last_confirmed_ms": last.last_confirmed_ms,
                    "id": last.lifecycle_id,
                }
            )
        return {
            "items": [opportunity_to_public(lifecycle) for lifecycle in items],
            "next_cursor": next_cursor,
        }

    @app.get("/api/opportunities/{lifecycle_id}")
    async def opportunity_detail(lifecycle_id: str) -> dict[str, Any]:
        for lifecycle in resolved_services.scanner_runtime.active():
            if lifecycle.lifecycle_id == lifecycle_id:
                return opportunity_to_public(lifecycle)
        try:
            stored = await resolved_services.scanner_runtime.stored_lifecycle(lifecycle_id)
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail={"code": "storage_not_ready"}) from error
        if stored is None:
            raise HTTPException(status_code=404, detail={"code": "opportunity_not_found"})
        return audit_snapshot_to_public(stored.snapshot_json)

    @app.get("/api/history")
    async def history(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
        route: str | None = None,
        from_time: Annotated[datetime | None, Query(alias="from")] = None,
        to_time: Annotated[datetime | None, Query(alias="to")] = None,
    ) -> dict[str, Any]:
        decoded = _cursor_decode(cursor, "history")
        from_ms = _datetime_ms(from_time)
        to_ms = _datetime_ms(to_time)
        if from_ms is not None and to_ms is not None and from_ms > to_ms:
            raise HTTPException(status_code=422, detail={"code": "invalid_time_range"})
        try:
            rows = await resolved_services.scanner_runtime.stored_lifecycles(state="closed")
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail={"code": "storage_not_ready"}) from error
        filtered = [
            row
            for row in rows
            if (route is None or row.route_id == route)
            and (from_ms is None or (row.closed_at_ms or 0) >= from_ms)
            and (to_ms is None or (row.closed_at_ms or 0) <= to_ms)
        ]
        filtered.sort(key=lambda row: (-(row.closed_at_ms or 0), row.lifecycle_id))
        if decoded is not None:
            try:
                cursor_key = (-int(decoded["closed_at_ms"]), str(decoded["id"]))
            except (KeyError, ValueError, TypeError):
                raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
            filtered = [
                row for row in filtered if (-(row.closed_at_ms or 0), row.lifecycle_id) > cursor_key
            ]
        page = filtered[: limit + 1]
        items = page[:limit]
        next_cursor = None
        if len(page) > limit:
            last = items[-1]
            next_cursor = _cursor_encode(
                {
                    "kind": "history",
                    "closed_at_ms": last.closed_at_ms,
                    "id": last.lifecycle_id,
                }
            )
        return {
            "items": [audit_snapshot_to_public(row.snapshot_json) for row in items],
            "next_cursor": next_cursor,
        }

    @app.websocket("/ws/opportunities")
    async def opportunity_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = resolved_services.hub.subscribe()
        try:
            snapshot = await resolved_services.snapshot_message()
            pending: list[dict[str, Any]] = []
            while not queue.empty():
                queued = queue.get_nowait()
                if queued is None:
                    await websocket.close(code=1013, reason="client fell behind; reconnect")
                    return
                if queued["sequence"] > snapshot["sequence"]:
                    pending.append(queued)
            await websocket.send_json(snapshot)
            for message in pending:
                await websocket.send_json(message)
            while True:
                message = await queue.get()
                if message is None:
                    await websocket.close(
                        code=1013, reason="client fell behind; reconnect for snapshot"
                    )
                    return
                await websocket.send_json(message)
        except WebSocketDisconnect:
            return
        finally:
            resolved_services.hub.unsubscribe(queue)

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:

        @app.get("/")
        async def root() -> dict[str, str]:
            return {
                "service": "tri-arb-scanner",
                "status": "frontend_not_built",
                "hint": "run pnpm --dir frontend build",
            }

    return app


app = create_app()
