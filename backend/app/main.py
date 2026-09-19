from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.shipments import router as shipments_router
from app.parsing.user_messages import humanize_exception
from app.services.opencode_review import ping_opencode, probe_opencode

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Customs Declaration Workspace",
        version="0.1.0",
        description="Document reconciliation & Excel export (profiles 18233 / BEIJING)",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(shipments_router)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return JSONResponse(
            status_code=500,
            content={"detail": humanize_exception(exc)},
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/opencode")
    def opencode_health() -> dict[str, Any]:
        return probe_opencode()

    @app.post("/api/health/opencode/ping")
    def opencode_ping() -> dict[str, Any]:
        return ping_opencode()

    if FRONTEND_DIR.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets", html=False), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(
                FRONTEND_DIR / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

    return app


app = create_app()
