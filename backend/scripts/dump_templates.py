"""Dump expected output layouts for template-based export."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2] / "materials" / "MVP_18233"
OUT = Path(__file__).resolve().parents[2] / "_mvp18233_templates_dump.txt"


def find_outputs(kit: str) -> dict[str, Path]:
    folder = next(p for p in ROOT.rglob(f"*{kit}*") if p.is_dir() and "02_" in p.name)
    # parent kit folder's 02_* 
    kit_dir = next(p for p in ROOT.iterdir() if p.is_dir())
    # walk
    out: dict[str, Path] = {}
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if kit not in p.name:
            continue
        # only in готовые
        parts = [x.lower() for x in p.parts]
        if not any("готов" in x or "result" in x or "02_" in x for x in parts):
            # fallback: size heuristic for outputs
            pass
        name_u = p.name.upper()
        if "ИНВОЙС" in p.name.upper() or "INVOICE" in name_u and "YS-RMB" not in name_u:
            if p.stat().st_size > 50000:
                out["invoice"] = p
        if "ПАКИНГ" in p.name.upper() or (p.stat().st_size > 50000 and "PACK" in name_u):
            if "ПАКИНГ" in p.name or ("PACKING" not in name_u and "ПАКИНГ" in p.name):
                out.setdefault("packing", p)
        if "СПЕЦИФИКАЦИЯ" in p.name or "СПЕЦИФ" in p.name:
            out["specification"] = p
    # more reliable by folder name containing 02_
    out = {}
    for p in ROOT.rglob("*"):
        if not p.is_file() or kit not in p.parent.parent.name and kit not in "".join(p.parts):
            continue
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ROOT))
        if f"0{kit[-1]}" not in rel and kit not in rel:
            continue
        if "02_" not in rel:
            continue
        if kit not in p.name:
            continue
        n = p.name
        if "ИНВОЙС" in n or "инвойс" in n.lower():
            out["invoice"] = p
        elif "ПАКИНГ" in n or "пакинг" in n.lower():
            out["packing"] = p
        elif "СПЕЦИФ" in n.upper() or "специф" in n.lower():
            out["specification"] = p
    return out


def dump_xlsx(path: Path, lines: list[str], max_rows: int = 55) -> None:
    lines.append(f"\n##### {path.name} #####")
    wb = load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        lines.append(f"-- sheet '{ws.title}' dims={ws.dimensions} max_row={ws.max_row} max_col={ws.max_column}")
        for r in range(1, min(ws.max_row, max_rows) + 1):
            vals = []
            for c in range(1, min(ws.max_column, 18) + 1):
                v = ws.cell(r, c).value
                if v is not None and str(v).strip() != "":
                    vals.append(f"{c}:{v}")
            if vals:
                lines.append(f"  R{r} " + " | ".join(vals))


def main() -> None:
    lines: list[str] = []
    for kit in ("626-1", "626-2"):
        lines.append(f"\n======== {kit} ========")
        outs = find_outputs(kit)
        lines.append(f"found: { {k: v.name for k,v in outs.items()} }")
        for kind, path in outs.items():
            dump_xlsx(path, lines, max_rows=60 if kind != "specification" else 80)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT)
    print("keys ok")


if __name__ == "__main__":
    main()
