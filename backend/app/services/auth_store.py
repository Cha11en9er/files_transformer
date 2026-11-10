"""File-backed accounts, sessions and action journal.

SQLite file under AUTH_ROOT. Passwords are PBKDF2 hashes. Refresh tokens are
stored only as hashes so logout and block kill the session on the server.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt

log = logging.getLogger("app.auth")

_LOGIN_RE = re.compile(r"^[A-Za-z0-9@._+-]{1,120}$")
_PBKDF2_ROUNDS = 200_000
_ACCESS_MINUTES = 15
_REFRESH_DAYS = 14
_ALGO = "HS256"


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def auth_root() -> Path:
    raw = (os.getenv("AUTH_ROOT") or "").strip()
    root = Path(raw) if raw else Path(__file__).resolve().parents[2] / "data" / "auth"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_path() -> Path:
    return auth_root() / "auth.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                login TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT '',
                login TEXT NOT NULL,
                action TEXT NOT NULL,
                shipment_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                invoice_no TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at)"
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def secret() -> str:
    env = (os.getenv("AUTH_SECRET") or "").strip()
    if env:
        return env
    path = auth_root() / "secret.txt"
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    text = secrets.token_urlsafe(32)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return text


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, salt_hex, digest_hex = stored.split("$", 2)
    except ValueError:
        return False
    if kind != "pbkdf2":
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return secrets.compare_digest(digest.hex(), digest_hex)


def validate_login(login: str) -> str:
    text = (login or "").strip()
    if not _LOGIN_RE.fullmatch(text):
        raise AuthError("Логин: латиница, цифры и символы @ . _ + -")
    return text


def validate_password(password: str) -> str:
    text = password or ""
    if len(text) < 5:
        raise AuthError("Пароль от 5 символов")
    return text


def _row_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "login": row["login"],
        "role": row["role"],
        "blocked": bool(row["blocked"]),
        "created_at": row["created_at"],
    }


def get_user(user_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_user(row)


def get_by_login(login: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
    return _row_user(row)


def list_users() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    return [_row_user(row) for row in rows if row is not None]


def create_user(*, login: str, password: str, role: str) -> dict[str, Any]:
    login_text = validate_login(login)
    password_text = validate_password(password)
    if role not in {"admin", "worker"}:
        raise AuthError("Роль: admin или worker")
    init_db()
    user_id = str(uuid.uuid4())
    now = _iso(_now())
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, login, password_hash, role, blocked, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (user_id, login_text, hash_password(password_text), role, now),
            )
    except sqlite3.IntegrityError as exc:
        raise AuthError("Такой логин уже есть") from exc
    user = get_user(user_id)
    assert user is not None
    return user


def _active_admins(conn: sqlite3.Connection, except_id: str = "") -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND blocked = 0 AND id != ?",
        (except_id,),
    ).fetchone()
    return int(row["n"] if row else 0)


def set_blocked(user_id: str, blocked: bool) -> dict[str, Any]:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AuthError("Пользователь не найден", 404)
        if blocked and row["role"] == "admin" and not row["blocked"]:
            if _active_admins(conn, except_id=user_id) < 1:
                raise AuthError("Нельзя заблокировать последнего активного администратора")
        conn.execute("UPDATE users SET blocked = ? WHERE id = ?", (1 if blocked else 0, user_id))
        if blocked:
            conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))
    user = get_user(user_id)
    assert user is not None
    return user


def set_role(user_id: str, role: str) -> dict[str, Any]:
    if role not in {"admin", "worker"}:
        raise AuthError("Роль: admin или worker")
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AuthError("Пользователь не найден", 404)
        if row["role"] == "admin" and role != "admin" and not row["blocked"]:
            if _active_admins(conn, except_id=user_id) < 1:
                raise AuthError("Нельзя снять права с последнего активного администратора")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    user = get_user(user_id)
    assert user is not None
    return user


def set_password(user_id: str, password: str) -> dict[str, Any]:
    password_text = validate_password(password)
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AuthError("Пользователь не найден", 404)
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password_text), user_id),
        )
        conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))
    user = get_user(user_id)
    assert user is not None
    return user


def authenticate(login: str, password: str) -> dict[str, Any]:
    login_text = (login or "").strip()
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE login = ?", (login_text,)).fetchone()
    if row is None or not verify_password(password or "", str(row["password_hash"])):
        log.warning("failed login for %s", login_text or "-")
        raise AuthError("Неверный логин или пароль", 401)
    if row["blocked"]:
        log.warning("blocked login for %s", login_text)
        raise AuthError("Учётка заблокирована", 403)
    user = _row_user(row)
    assert user is not None
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_refresh(user_id: str) -> tuple[str, str]:
    """Return (raw_token, session_id)."""
    init_db()
    raw = secrets.token_urlsafe(32)
    session_id = str(uuid.uuid4())
    expires = _iso(_now() + timedelta(days=_REFRESH_DAYS))
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, user_id, token_hash, expires_at, revoked)
            VALUES (?, ?, ?, ?, 0)
            """,
            (session_id, user_id, _token_hash(raw), expires),
        )
    return raw, session_id


def revoke_refresh(raw: str) -> None:
    if not raw:
        return
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE token_hash = ?",
            (_token_hash(raw),),
        )


def peek_refresh(raw: str) -> dict[str, Any] | None:
    """Valid refresh session without rotating it. Used to keep HTML pages open."""
    if not raw:
        return None
    init_db()
    now = _iso(_now())
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?",
            (_token_hash(raw),),
        ).fetchone()
        if row is None or row["revoked"] or str(row["expires_at"]) < now:
            return None
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    user = _row_user(user_row)
    if user is None or user["blocked"]:
        return None
    return user


def consume_refresh(raw: str) -> dict[str, Any] | None:
    """Validate refresh token and rotate it. Returns user or None."""
    if not raw:
        return None
    init_db()
    now = _iso(_now())
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?",
            (_token_hash(raw),),
        ).fetchone()
        if row is None or row["revoked"] or str(row["expires_at"]) < now:
            return None
        conn.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (row["id"],))
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    user = _row_user(user_row)
    if user is None or user["blocked"]:
        return None
    return user


def encode_access(user: dict[str, Any]) -> str:
    payload = {
        "sub": user["id"],
        "login": user["login"],
        "role": user["role"],
        "typ": "access",
        "exp": _now() + timedelta(minutes=_ACCESS_MINUTES),
    }
    return jwt.encode(payload, secret(), algorithm=_ALGO)


def decode_access(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, secret(), algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    user = get_user(str(payload.get("sub") or ""))
    if user is None or user["blocked"]:
        return None
    if user["role"] != payload.get("role") or user["login"] != payload.get("login"):
        # Role or login changed: force a new login via refresh, which re-reads the row.
        return None
    return user


def record_audit(
    user: dict[str, Any] | None,
    action: str,
    *,
    shipment_id: str = "",
    title: str = "",
    invoice_no: str = "",
    detail: str = "",
    login: str = "",
) -> None:
    init_db()
    who = user or {}
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_events (
                id, created_at, user_id, login, action, shipment_id, title, invoice_no, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                _iso(_now()),
                str(who.get("id") or ""),
                str(login or who.get("login") or ""),
                action,
                shipment_id or "",
                title or "",
                invoice_no or "",
                detail or "",
            ),
        )


def blocked_logins() -> set[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT login FROM users WHERE blocked = 1").fetchall()
    return {str(row["login"]) for row in rows}


def list_audit(*, login: str = "", query: str = "", date_from: str = "", date_to: str = "") -> list[dict[str, Any]]:
    """Grouped feed: one card per shipment (or per lone event), details inside."""
    init_db()
    sql = "SELECT * FROM audit_events WHERE 1=1"
    args: list[Any] = []
    if login.strip():
        sql += " AND login = ?"
        args.append(login.strip())
    if date_from.strip():
        sql += " AND created_at >= ?"
        args.append(date_from.strip())
    if date_to.strip():
        # date input is YYYY-MM-DD; include the whole end day
        end = date_to.strip()
        if len(end) == 10:
            end = end + "T23:59:59Z"
        sql += " AND created_at <= ?"
        args.append(end)
    sql += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, args).fetchall()]
    needle = query.strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle in str(row.get("title") or "").lower()
            or needle in str(row.get("invoice_no") or "").lower()
            or needle in str(row.get("login") or "").lower()
        ]
    muted = blocked_logins()
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        sid = str(row.get("shipment_id") or "")
        key = sid or f"event:{row['id']}"
        if key not in groups:
            groups[key] = {
                "key": key,
                "shipment_id": sid,
                "title": row.get("title") or "",
                "invoice_no": row.get("invoice_no") or "",
                "login": row.get("login") or "",
                "login_blocked": str(row.get("login") or "") in muted,
                "latest_at": row.get("created_at") or "",
                "count": 0,
                "events": [],
            }
            order.append(key)
        bucket = groups[key]
        bucket["count"] += 1
        if not bucket["title"] and row.get("title"):
            bucket["title"] = row["title"]
        if not bucket["invoice_no"] and row.get("invoice_no"):
            bucket["invoice_no"] = row["invoice_no"]
        bucket["events"].append(
            {
                "id": row["id"],
                "at": row["created_at"],
                "action": row["action"],
                "login": row["login"],
                "detail": row["detail"],
                "login_blocked": str(row.get("login") or "") in muted,
            }
        )
    return [groups[key] for key in order]


def can_see_shipment(user: dict[str, Any], item: dict[str, Any]) -> bool:
    if user.get("role") == "admin":
        return True
    owner = str(item.get("owner_user_id") or "")
    return bool(owner) and owner == str(user.get("id") or "")
