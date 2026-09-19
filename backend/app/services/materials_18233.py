"""Locate 18233-style sources and эталон templates.

Looks in materials/MVP_18233 first, then documents/ (ханчжоу 626-1/626-2, 18080, 18312).
626-1 / 626-2 here are layout kits (fabric vs leather), not job numbers.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MATERIALS_ROOT = PROJECT_ROOT / "materials" / "MVP_18233"
DOCUMENTS_ROOT = PROJECT_ROOT / "documents"

_SOURCE_DIR_NAMES = ("01", "исходники", "исходные")
_READY_DIR_NAMES = ("02", "эталон", "готовые")


def _search_roots() -> list[Path]:
    roots: list[Path] = []
    if MATERIALS_ROOT.exists():
        roots.append(MATERIALS_ROOT)
    if DOCUMENTS_ROOT.exists():
        roots.append(DOCUMENTS_ROOT)
    return roots


def _child_named(base: Path, names: tuple[str, ...]) -> Path | None:
    exact: list[Path] = []
    prefixed: list[Path] = []
    lowered_names = tuple(name.lower() for name in names)
    if not base.exists():
        return None
    for child in base.iterdir():
        if not child.is_dir():
            continue
        lowered = child.name.lower()
        if lowered in lowered_names:
            exact.append(child)
        elif any(lowered.startswith(name) for name in lowered_names):
            prefixed.append(child)
    if exact:
        return exact[0]
    if prefixed:
        return prefixed[0]
    return None


def _kit_dir_matches(name: str, kit: str) -> bool:
    lowered = name.lower()
    wanted = kit.lower()
    return (
        lowered == wanted
        or lowered.endswith(wanted)
        or lowered.startswith(f"{wanted} ")
        or lowered.startswith(wanted)
    )


def kit_base(kit: str) -> Path:
    wanted = (kit or "").strip()
    if not wanted:
        raise FileNotFoundError("empty kit")
    matches: list[Path] = []
    for root in _search_roots():
        for path in root.rglob("*"):
            if not path.is_dir() or not _kit_dir_matches(path.name, wanted):
                continue
            if _child_named(path, _SOURCE_DIR_NAMES) or _child_named(path, _READY_DIR_NAMES):
                matches.append(path)
    if not matches:
        raise FileNotFoundError(wanted)
    matches.sort(key=lambda path: (0 if "ханчжоу" in path.name.lower() else 1, len(str(path))))
    return matches[0]


def _pick_file(files: list[Path], *needles: str) -> Path:
    upper_needles = tuple(n.upper() for n in needles)
    for path in files:
        name = path.name.upper()
        if any(needle in name for needle in upper_needles):
            return path
    raise FileNotFoundError(f"no file matching {needles} in {[p.name for p in files]}")


@lru_cache(maxsize=8)
def kit_sources(kit: str) -> dict[str, Path]:
    base = kit_base(kit)
    src = _child_named(base, _SOURCE_DIR_NAMES)
    if src is None:
        raise FileNotFoundError(f"no source folder in {base}")
    files = [path for path in src.iterdir() if path.is_file()]
    return {
        "invoice": _pick_file(files, "INVOICE", "ИНВОЙС"),
        "packing": _pick_file(files, "PACK", "-PL", "_PL", "ПАКИНГ", "УПАКОВ"),
        "specification": _pick_file(files, "SPEC", "СПЕЦИФ"),
    }


@lru_cache(maxsize=8)
def kit_templates(kit: str) -> dict[str, Path]:
    base = kit_base(kit)
    ready = _child_named(base, _READY_DIR_NAMES)
    if ready is None:
        raise FileNotFoundError(f"no etalon folder in {base}")
    files = [path for path in ready.iterdir() if path.is_file() and path.suffix.lower() in {".xlsx", ".xls"}]
    return {
        "invoice": _pick_file(files, "ИНВОЙС", "INVOICE"),
        "packing": _pick_file(files, "ПАКИНГ", "PACK", "УПАКОВ"),
        "specification": _pick_file(files, "СПЕЦИФ", "SPEC"),
    }


@lru_cache(maxsize=1)
def materials_available() -> bool:
    try:
        templates = kit_templates("626-1")
    except Exception:
        return False
    return all(path.exists() for path in templates.values())
