"""Session cookies for API routes."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.services import auth_store

ACCESS_COOKIE = "ft_access"
REFRESH_COOKIE = "ft_refresh"


def user_from_request(request: Request, *, allow_refresh: bool = False) -> dict | None:
    user = auth_store.decode_access(request.cookies.get(ACCESS_COOKIE) or "")
    if user is not None:
        return user
    if allow_refresh:
        return auth_store.peek_refresh(request.cookies.get(REFRESH_COOKIE) or "")
    return None


def current_user(request: Request) -> dict:
    user = user_from_request(request, allow_refresh=False)
    if user is None:
        raise HTTPException(status_code=401, detail="Нужен вход")
    return user


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    return user
