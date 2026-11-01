"""Persistent shipment history: sources, workspace JSON, export ZIPs.

Shared site-wide store (no auth). Manual delete only — no TTL.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "history"
_MOSCOW = ZoneInfo("Europe/Moscow")
_SAFE_NAME = re.compile(r"[^\w.\-() +\u0400-\u04FF]+", re.UNICODE)


def history_root() -> Path:
    raw = (os.getenv("HISTORY_ROOT") or "").strip()
    root = Path(raw) if raw else _DEFAULT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_path() -> Path:
    return history_root() / "history.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Commit/rollback via connection context, then always close (Windows file locks)."""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_history_db() -> None:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shipments (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                profile_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_bytes INTEGER NOT NULL DEFAULT 0,
                export_bytes INTEGER NOT NULL DEFAULT 0,
                source_count INTEGER NOT NULL DEFAULT 0,
                export_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def moscow_label(iso_utc: str) -> str:
    """Human datetime in Europe/Moscow for UI."""
    text = (iso_utc or "").strip()
    if not text:
        return ""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return iso_utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_MOSCOW)
    return local.strftime("%d.%m.%Y %H:%M")


def _shipment_dir(shipment_id: str) -> Path:
    return history_root() / shipment_id


def _meta_path(shipment_id: str) -> Path:
    return _shipment_dir(shipment_id) / "meta.json"


def _workspace_path(shipment_id: str) -> Path:
    return _shipment_dir(shipment_id) / "workspace.json"


def _sources_dir(shipment_id: str) -> Path:
    return _shipment_dir(shipment_id) / "sources"


def _exports_dir(shipment_id: str) -> Path:
    return _shipment_dir(shipment_id) / "exports"


def _safe_filename(name: str) -> str:
    base = Path(str(name or "file").replace("\\", "/")).name or "file"
    cleaned = _SAFE_NAME.sub("_", base).strip(" ._") or "file"
    return cleaned[:180]


def _unique_name(folder: Path, name: str) -> str:
    candidate = _safe_filename(name)
    if not (folder / candidate).exists():
        return candidate
    stem = Path(candidate).stem
    suffix = Path(candidate).suffix
    for _ in range(50):
        alt = f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"
        if not (folder / alt).exists():
            return alt
    return f"{stem}_{uuid.uuid4().hex}{suffix}"


def _dir_bytes(folder: Path) -> tuple[int, int]:
    if not folder.is_dir():
        return 0, 0
    total = 0
    count = 0
    for path in folder.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
            count += 1
    return total, count


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _refresh_sizes(shipment_id: str) -> dict[str, int]:
    src_bytes, src_count = _dir_bytes(_sources_dir(shipment_id))
    exp_bytes, exp_count = _dir_bytes(_exports_dir(shipment_id))
    with _db() as conn:
        conn.execute(
            """
            UPDATE shipments
            SET source_bytes = ?, export_bytes = ?, source_count = ?, export_count = ?
            WHERE id = ?
            """,
            (src_bytes, exp_bytes, src_count, exp_count, shipment_id),
        )
        conn.commit()
    return {
        "source_bytes": src_bytes,
        "export_bytes": exp_bytes,
        "source_count": src_count,
        "export_count": exp_count,
    }


def _list_files(folder: Path, kind: str) -> list[dict[str, Any]]:
    if not folder.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        out.append(
            {
                "kind": kind,
                "name": path.name,
                "bytes": size,
                "updated_at": _iso(mtime),
                "updated_at_moscow": moscow_label(_iso(mtime)),
            }
        )
    return out


def create_draft(
    *,
    title: str,
    profile_type: str,
    workspace: dict[str, Any],
    sources: list[tuple[Path, str]],
    catalog_names: set[str] | None = None,
    shipment_id: str | None = None,
) -> str:
    """Copy source files and write workspace. Returns history id."""
    init_history_db()
    sid = (shipment_id or str(uuid.uuid4())).strip() or str(uuid.uuid4())
    now = _iso(_utc_now())
    root = _shipment_dir(sid)
    if root.exists():
        raise FileExistsError(f"history entry already exists: {sid}")
    sources_dir = _sources_dir(sid)
    exports_dir = _exports_dir(sid)
    sources_dir.mkdir(parents=True, exist_ok=False)
    exports_dir.mkdir(parents=True, exist_ok=True)

    catalog = {str(n).lower() for n in (catalog_names or set())}
    copied: list[dict[str, Any]] = []
    for src, display in sources:
        path = Path(src)
        if not path.is_file():
            continue
        name = _unique_name(sources_dir, display or path.name)
        target = sources_dir / name
        shutil.copy2(path, target)
        is_catalog = Path(str(display or path.name).replace("\\", "/")).name.lower() in catalog
        copied.append({"name": name, "original": Path(str(display or path.name).replace("\\", "/")).name, "catalog": is_catalog})

    title_text = (title or "").strip() or "поставка"
    profile = str(profile_type or "").strip() or "18233"
    meta = {
        "id": sid,
        "title": title_text,
        "profile_type": profile,
        "created_at": now,
        "updated_at": now,
        "sources": copied,
        "export_version": 0,
    }
    _write_json(_meta_path(sid), meta)
    ws = dict(workspace or {})
    ws["history_id"] = sid
    ws["title"] = title_text
    _write_json(_workspace_path(sid), ws)

    sizes = _dir_bytes(sources_dir)
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO shipments (
                id, title, profile_type, created_at, updated_at,
                source_bytes, export_bytes, source_count, export_count
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0)
            """,
            (sid, title_text, profile, now, now, sizes[0], sizes[1]),
        )
        conn.commit()
    return sid


def list_shipments() -> list[dict[str, Any]]:
    init_history_db()
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, profile_type, created_at, updated_at,
                   source_bytes, export_bytes, source_count, export_count
            FROM shipments
            ORDER BY updated_at DESC
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["created_at_moscow"] = moscow_label(item["created_at"])
        item["updated_at_moscow"] = moscow_label(item["updated_at"])
        item["total_bytes"] = int(item["source_bytes"] or 0) + int(item["export_bytes"] or 0)
        out.append(item)
    return out


def get_shipment(shipment_id: str) -> dict[str, Any] | None:
    init_history_db()
    sid = str(shipment_id or "").strip()
    if not sid or not _shipment_dir(sid).is_dir():
        return None
    with _db() as conn:
        row = conn.execute("SELECT * FROM shipments WHERE id = ?", (sid,)).fetchone()
    if row is None:
        # Disk orphan: rebuild index row from meta if possible
        meta_path = _meta_path(sid)
        if not meta_path.is_file():
            return None
        meta = _read_json(meta_path)
        sizes = _refresh_sizes(sid)
        with _db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO shipments (
                    id, title, profile_type, created_at, updated_at,
                    source_bytes, export_bytes, source_count, export_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    meta.get("title") or "поставка",
                    meta.get("profile_type") or "18233",
                    meta.get("created_at") or _iso(_utc_now()),
                    meta.get("updated_at") or _iso(_utc_now()),
                    sizes["source_bytes"],
                    sizes["export_bytes"],
                    sizes["source_count"],
                    sizes["export_count"],
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM shipments WHERE id = ?", (sid,)).fetchone()
    assert row is not None
    meta = _read_json(_meta_path(sid)) if _meta_path(sid).is_file() else {}
    workspace = None
    if _workspace_path(sid).is_file():
        try:
            workspace = _read_json(_workspace_path(sid))
        except (OSError, json.JSONDecodeError):
            workspace = None
    item = dict(row)
    item["created_at_moscow"] = moscow_label(item["created_at"])
    item["updated_at_moscow"] = moscow_label(item["updated_at"])
    item["total_bytes"] = int(item["source_bytes"] or 0) + int(item["export_bytes"] or 0)
    item["meta"] = meta
    item["sources"] = _list_files(_sources_dir(sid), "sources")
    item["exports"] = _list_files(_exports_dir(sid), "exports")
    item["workspace"] = workspace
    return item


def rename_shipment(shipment_id: str, title: str) -> dict[str, Any] | None:
    sid = str(shipment_id or "").strip()
    title_text = (title or "").strip()
    if not sid or not title_text:
        return None
    if get_shipment(sid) is None:
        return None
    now = _iso(_utc_now())
    meta_path = _meta_path(sid)
    meta = _read_json(meta_path) if meta_path.is_file() else {"id": sid}
    meta["title"] = title_text
    meta["updated_at"] = now
    _write_json(meta_path, meta)
    if _workspace_path(sid).is_file():
        try:
            ws = _read_json(_workspace_path(sid))
            ws["title"] = title_text
            _write_json(_workspace_path(sid), ws)
        except (OSError, json.JSONDecodeError):
            pass
    with _db() as conn:
        conn.execute(
            "UPDATE shipments SET title = ?, updated_at = ? WHERE id = ?",
            (title_text, now, sid),
        )
        conn.commit()
    return get_shipment(sid)


def save_workspace(
    shipment_id: str,
    workspace: dict[str, Any],
    *,
    title: str | None = None,
) -> dict[str, Any] | None:
    sid = str(shipment_id or "").strip()
    if not sid or get_shipment(sid) is None:
        return None
    now = _iso(_utc_now())
    ws = dict(workspace or {})
    ws["history_id"] = sid
    title_text = (title if title is not None else ws.get("title") or "").strip()
    if title_text:
        ws["title"] = title_text
    _write_json(_workspace_path(sid), ws)
    meta_path = _meta_path(sid)
    meta = _read_json(meta_path) if meta_path.is_file() else {"id": sid}
    if title_text:
        meta["title"] = title_text
    meta["updated_at"] = now
    _write_json(meta_path, meta)
    with _db() as conn:
        if title_text:
            conn.execute(
                "UPDATE shipments SET title = ?, updated_at = ? WHERE id = ?",
                (title_text, now, sid),
            )
        else:
            conn.execute("UPDATE shipments SET updated_at = ? WHERE id = ?", (now, sid))
        conn.commit()
    return get_shipment(sid)


def store_export_zip(shipment_id: str, data: bytes, *, filename: str | None = None) -> dict[str, Any] | None:
    sid = str(shipment_id or "").strip()
    if not sid or get_shipment(sid) is None:
        return None
    exports = _exports_dir(sid)
    exports.mkdir(parents=True, exist_ok=True)
    meta_path = _meta_path(sid)
    meta = _read_json(meta_path) if meta_path.is_file() else {"id": sid}
    version = int(meta.get("export_version") or 0) + 1
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    hint = _safe_filename(filename or "export.zip")
    if not hint.lower().endswith(".zip"):
        hint = f"{Path(hint).stem}.zip"
    name = f"v{version:03d}_{stamp}_{hint}"
    target = exports / name
    target.write_bytes(data)
    now = _iso(_utc_now())
    meta["export_version"] = version
    meta["updated_at"] = now
    meta["last_export"] = name
    _write_json(meta_path, meta)
    with _db() as conn:
        conn.execute("UPDATE shipments SET updated_at = ? WHERE id = ?", (now, sid))
        conn.commit()
    _refresh_sizes(sid)
    return {
        "name": name,
        "bytes": len(data),
        "updated_at": now,
        "updated_at_moscow": moscow_label(now),
        "kind": "exports",
    }


def resolve_file(shipment_id: str, kind: str, name: str) -> Path | None:
    sid = str(shipment_id or "").strip()
    file_name = Path(str(name or "").replace("\\", "/")).name
    if not sid or not file_name or file_name in {".", ".."}:
        return None
    kind_key = str(kind or "").strip().lower()
    if kind_key in {"source", "sources"}:
        folder = _sources_dir(sid)
    elif kind_key in {"export", "exports"}:
        folder = _exports_dir(sid)
    else:
        return None
    path = (folder / file_name).resolve()
    try:
        path.relative_to(folder.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def delete_file(shipment_id: str, kind: str, name: str) -> bool:
    path = resolve_file(shipment_id, kind, name)
    if path is None:
        return False
    path.unlink(missing_ok=True)
    sid = str(shipment_id).strip()
    now = _iso(_utc_now())
    with _db() as conn:
        conn.execute("UPDATE shipments SET updated_at = ? WHERE id = ?", (now, sid))
        conn.commit()
    _refresh_sizes(sid)
    meta_path = _meta_path(sid)
    if meta_path.is_file():
        try:
            meta = _read_json(meta_path)
            meta["updated_at"] = now
            _write_json(meta_path, meta)
        except (OSError, json.JSONDecodeError):
            pass
    return True


def delete_shipment(shipment_id: str) -> bool:
    sid = str(shipment_id or "").strip()
    if not sid:
        return False
    root = _shipment_dir(sid)
    existed = root.is_dir()
    with _db() as conn:
        conn.execute("DELETE FROM shipments WHERE id = ?", (sid,))
        conn.commit()
    if existed:
        shutil.rmtree(root, ignore_errors=True)
    return existed


def total_disk_bytes() -> int:
    init_history_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(source_bytes + export_bytes), 0) AS total FROM shipments"
        ).fetchone()
    return int(row["total"] if row else 0)
