"""Local-only FastAPI application shell."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from tri_arb import __version__
from tri_arb.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    app = FastAPI(title="Tri-Arb Scanner", version=__version__)
    app.state.settings = resolved

    @app.get("/api/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "service": "tri-arb-scanner", "version": __version__}

    @app.get("/api/health/ready")
    async def health_ready() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "market data pipeline is not implemented yet",
            },
        )

    @app.get("/api/config")
    async def public_config() -> dict[str, str | int]:
        return resolved.public_dict()

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
