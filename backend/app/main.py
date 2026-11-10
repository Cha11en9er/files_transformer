from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.deps import user_from_request
from app.api.routes.auth import router as auth_router
from app.api.routes.history import router as history_router
from app.api.routes.shipments import router as shipments_router
from app.parsing.user_messages import humanize_exception
from app.services.auth_store import init_db as init_auth_db
from app.services.history_store import init_history_db
from app.services.opencode_review import ping_opencode, probe_opencode

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
_PUBLIC_API = {"/api/health", "/api/v1/auth/login", "/api/v1/auth/refresh"}


def _page(name: str) -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / name,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


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
    app.include_router(auth_router)
    app.include_router(shipments_router)
    app.include_router(history_router)
    try:
        init_history_db()
    except OSError:
        pass
    try:
        init_auth_db()
    except OSError:
        pass

    @app.middleware("http")
    async def _require_session(request: Request, call_next):
        path = request.url.path
        if path.startswith("/assets/") or path == "/login" or path in _PUBLIC_API:
            return await call_next(request)
        if path.startswith("/api/"):
            if user_from_request(request) is None:
                return JSONResponse(status_code=401, content={"detail": "Нужен вход"})
        return await call_next(request)

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

        def _gate(request: Request, page: str, *, admin: bool = False):
            user = user_from_request(request, allow_refresh=True)
            if user is None:
                return RedirectResponse("/login", status_code=303)
            if admin and user.get("role") != "admin":
                return RedirectResponse("/", status_code=303)
            return _page(page)

        @app.get("/login")
        def login_page(request: Request):
            if user_from_request(request, allow_refresh=True) is not None:
                return RedirectResponse("/", status_code=303)
            return _page("login.html")

        @app.get("/")
        def index(request: Request):
            return _gate(request, "index.html")

        @app.get("/history")
        def history_page(request: Request):
            return _gate(request, "history.html")

        @app.get("/users")
        def users_page(request: Request):
            return _gate(request, "users.html", admin=True)

        @app.get("/audit")
        def audit_page(request: Request):
            return _gate(request, "audit.html", admin=True)

    return app


app = create_app()
