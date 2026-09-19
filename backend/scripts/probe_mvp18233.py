"""Probe real MVP_18233 materials and print acceptance counts."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2] / "materials" / "MVP_18233"
OUT = Path(__file__).resolve().parents[2]


def find(name: str) -> Path:
    return next(p for p in ROOT.rglob(name))


def aggregate_spec(path: Path) -> dict[str, dict]:
    sdf = pd.read_excel(path, header=None, dtype=object)
    agg: dict[str, dict] = defaultdict(
        lambda: {"rolls": 0, "meters": 0.0, "area": 0.0, "nw": 0.0, "gw": 0.0, "price": None, "width": None}
    )
    for i in range(7, len(sdf)):
        name = sdf.iloc[i, 2]
        if pd.isna(name):
            continue
        name = str(name).strip()
        if not name or name.upper().startswith("TOTAL"):
            continue

        def num(col: int) -> float:
            v = sdf.iloc[i, col]
            return float(v) if pd.notna(v) else 0.0

        a = agg[name]
        a["rolls"] += 1
        a["meters"] += num(5)
        a["area"] += num(7)
        a["nw"] += num(8)
        a["gw"] += num(9)
        if pd.notna(sdf.iloc[i, 4]):
            a["price"] = float(sdf.iloc[i, 4])
        if pd.notna(sdf.iloc[i, 6]):
            a["width"] = float(sdf.iloc[i, 6])
    return dict(agg)


def invoice_articles(path: Path) -> list[str]:
    df = pd.read_excel(path, header=None, dtype=object)
    arts: list[str] = []
    for i in range(8, len(df)):
        design = df.iloc[i, 1]
        no = df.iloc[i, 0]
        if pd.isna(design):
            continue
        text = str(design).replace("\n", " ").strip()
        upper = text.upper()
        if any(tok in upper for tok in ("SOFA FABRIC", "ARTIFICIAL LEATHER", "TOTAL")):
            continue
        if pd.notna(no):
            arts.append(text)
    return arts


def main() -> None:
    lines: list[str] = []
    for kit in ("626-1", "626-2"):
        inv = find(f"{kit}-YS-RMB-EXW-INVOICE.XLSX")
        pl = find(f"{kit}-YS-RMB-EXW-PL.XLSX")
        sp = find(f"{kit}-YS-RMB-EXW-Specification.xls")
        arts = invoice_articles(inv)
        agg = aggregate_spec(sp)
        lines.append(f"=== {kit} ===")
        lines.append(f"invoice articles: {len(arts)}")
        for a in arts:
            lines.append(f"  INV {a}")
        lines.append(f"spec aggregates: {len(agg)}")
        for name, v in agg.items():
            lines.append(
                f"  SPEC {name}: rolls={v['rolls']} m={round(v['meters'], 2)} "
                f"m2={round(v['area'], 2)} nw={round(v['nw'], 2)} gw={round(v['gw'], 2)}"
            )
        missing = [a for a in arts if a not in agg]
        extra = [a for a in agg if a not in arts]
        lines.append(f"missing in spec: {missing}")
        lines.append(f"extra in spec: {extra}")
        lines.append(f"PL file: {pl.name}")
        lines.append("")
    out = OUT / "_mvp18233_probe.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
