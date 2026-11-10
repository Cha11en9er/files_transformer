"""Login, session refresh, user admin, action journal."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, current_user, require_admin, user_from_request
from app.services import auth_store
from app.services.auth_store import AuthError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    login: str
    password: str


class UserCreateIn(BaseModel):
    login: str
    password: str
    role: str = "worker"


class PasswordIn(BaseModel):
    password: str


class BlockIn(BaseModel):
    blocked: bool


class RoleIn(BaseModel):
    role: str


def _public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "login": user["login"],
        "role": user["role"],
        "blocked": bool(user.get("blocked")),
        "created_at": user.get("created_at") or "",
    }


def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


def _set_session(response: Response, request: Request, user: dict[str, Any]) -> None:
    access = auth_store.encode_access(user)
    refresh, _sid = auth_store.issue_refresh(user["id"])
    secure = _secure(request)
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=15 * 60,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=14 * 24 * 3600,
        path="/",
    )


def _clear_session(response: Response, request: Request) -> None:
    secure = _secure(request)
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(REFRESH_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")


def _raise(exc: AuthError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    try:
        user = auth_store.authenticate(payload.login, payload.password)
    except AuthError as exc:
        _raise(exc)
    _set_session(response, request, user)
    auth_store.record_audit(user, "login")
    return {"user": _public(user)}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    user = user_from_request(request, allow_refresh=True)
    auth_store.revoke_refresh(request.cookies.get(REFRESH_COOKIE) or "")
    if user is not None:
        auth_store.record_audit(user, "logout")
    _clear_session(response, request)
    return {"status": "ok"}


@router.post("/refresh")
def refresh(request: Request, response: Response) -> dict[str, Any]:
    user = auth_store.consume_refresh(request.cookies.get(REFRESH_COOKIE) or "")
    if user is None:
        _clear_session(response, request)
        raise HTTPException(status_code=401, detail="Сессия истекла")
    _set_session(response, request, user)
    return {"user": _public(user)}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    return {"user": _public(current_user(request))}


@router.get("/users")
def users(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"items": [_public(item) for item in auth_store.list_users()]}


@router.post("/users")
def create_user(payload: UserCreateIn, request: Request) -> dict[str, Any]:
    actor = require_admin(request)
    try:
        user = auth_store.create_user(login=payload.login, password=payload.password, role=payload.role)
    except AuthError as exc:
        _raise(exc)
    auth_store.record_audit(actor, "user_created", detail=f"{user['login']} · {user['role']}")
    return {"user": _public(user)}


@router.post("/users/{user_id}/password")
def change_password(user_id: str, payload: PasswordIn, request: Request, response: Response) -> dict[str, Any]:
    actor = require_admin(request)
    try:
        user = auth_store.set_password(user_id, payload.password)
    except AuthError as exc:
        _raise(exc)
    auth_store.record_audit(actor, "password_set", detail=user["login"])
    if actor["id"] == user["id"]:
        _set_session(response, request, user)
    return {"user": _public(user)}


@router.post("/users/{user_id}/block")
def block_user(user_id: str, payload: BlockIn, request: Request) -> dict[str, Any]:
    actor = require_admin(request)
    try:
        user = auth_store.set_blocked(user_id, payload.blocked)
    except AuthError as exc:
        _raise(exc)
    auth_store.record_audit(
        actor,
        "user_blocked" if payload.blocked else "user_unblocked",
        detail=user["login"],
    )
    return {"user": _public(user)}


@router.post("/users/{user_id}/role")
def change_role(user_id: str, payload: RoleIn, request: Request) -> dict[str, Any]:
    actor = require_admin(request)
    try:
        user = auth_store.set_role(user_id, payload.role)
    except AuthError as exc:
        _raise(exc)
    auth_store.record_audit(actor, "role_set", detail=f"{user['login']} · {user['role']}")
    return {"user": _public(user)}


@router.get("/audit")
def audit(
    request: Request,
    login: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    require_admin(request)
    items = auth_store.list_audit(login=login, query=q, date_from=date_from, date_to=date_to)
    return {"items": items, "count": len(items)}
