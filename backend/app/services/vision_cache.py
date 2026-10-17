"""Temporary JPEG cache for vision frames shown in the UI (TTL 1 hour)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

VISION_ROOT = Path(__file__).resolve().parents[2] / "data" / "vision_cache"
TTL_SEC = 60 * 60


def _purge_old(now: float | None = None) -> None:
    root = VISION_ROOT
    if not root.is_dir():
        return
    stamp = now if now is not None else time.time()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        marker = child / ".ts"
        try:
            created = float(marker.read_text(encoding="utf-8").strip()) if marker.is_file() else child.stat().st_mtime
        except OSError:
            created = 0.0
        if stamp - created > TTL_SEC:
            shutil.rmtree(child, ignore_errors=True)


def stash_vision_pages(token: str, pages: list[Any]) -> list[dict[str, Any]]:
    """Copy vision JPEGs under token and return public URL descriptors."""
    safe = "".join(ch for ch in str(token) if ch.isalnum() or ch in "-_")[:64]
    if not safe or not pages:
        return []
    _purge_old()
    dest = VISION_ROOT / safe
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".ts").write_text(str(time.time()), encoding="utf-8")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, page in enumerate(pages):
        src = Path(getattr(page, "path", "") or "")
        if not src.is_file():
            continue
        name = src.name or f"frame_{index + 1}.jpg"
        if name in seen:
            name = f"{src.stem}_{index + 1}{src.suffix or '.jpg'}"
        seen.add(name)
        target = dest / name
        try:
            shutil.copy2(src, target)
        except OSError:
            continue
        source = getattr(page, "source_name", None) or name
        out.append(
            {
                "name": name,
                "source": Path(str(source)).name,
                "page": getattr(page, "page", None),
                "url": f"/api/v1/shipments/vision/{safe}/{name}",
            }
        )
    return out


def resolve_vision_file(token: str, filename: str) -> Path | None:
    _purge_old()
    safe = "".join(ch for ch in str(token) if ch.isalnum() or ch in "-_")[:64]
    name = Path(str(filename or "")).name
    if not safe or not name or name.startswith("."):
        return None
    root = (VISION_ROOT / safe).resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None
