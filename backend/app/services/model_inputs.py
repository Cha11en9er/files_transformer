"""Рабочие папки исходников для модели на том же диске, где OpenCode.

Каждый запрос → MODEL_INPUTS_ROOT/postavka_{uuid}/ с копиями загруженных файлов.
Абсолютные POSIX-пути уходят в промпт; модель читает их через tool read.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path

# На update-сайте по умолчанию; локально — backend/data/model_inputs.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "model_inputs"
TTL_HOURS = 24


def model_inputs_root() -> Path:
    raw = (os.getenv("MODEL_INPUTS_ROOT") or "").strip()
    root = Path(raw) if raw else _DEFAULT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_postavka_dir() -> Path:
    """Создаёт postavka_{uuid} и подчищает старые папки по TTL."""
    cleanup_expired()
    folder = model_inputs_root() / f"postavka_{uuid.uuid4().hex}"
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def stash_reading(reading: list[tuple[Path, str]], dest: Path | None = None) -> list[Path]:
    """Копирует файлы поставки в postavka_*. Возвращает абсолютные пути копий."""
    folder = dest or new_postavka_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    used: set[str] = set()
    for src, display in reading:
        name = Path(str(display or src.name).replace("\\", "/")).name or src.name
        if name in used:
            stem = Path(name).stem
            suffix = Path(name).suffix
            name = f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
        used.add(name)
        target = folder / name
        shutil.copy2(src, target)
        out.append(target.resolve())
    return out


def cleanup_expired(ttl_hours: float | None = None) -> int:
    """Удаляет postavka_* старше TTL. Возвращает число удалённых папок."""
    hours = TTL_HOURS if ttl_hours is None else float(ttl_hours)
    raw = (os.getenv("MODEL_INPUTS_TTL_H") or "").strip()
    if raw:
        try:
            hours = float(raw)
        except ValueError:
            pass
    if hours <= 0:
        return 0
    root = model_inputs_root()
    cutoff = time.time() - hours * 3600
    removed = 0
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith("postavka_"):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed


def remove_postavka(folder: Path | None) -> None:
    if folder is None:
        return
    try:
        if folder.is_dir() and folder.name.startswith("postavka_"):
            shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass
