"""Compare transformer output vs customer etalon on 4_pravka kits."""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.transform.service import transform_paths, canonical_to_rows

DOCS = Path(__file__).resolve().parents[3] / "documents" / "4_pravka"
OUT = Path(__file__).resolve().parent / "out" / "90_transform_compare.txt"


def _sum(rows, *keys):
    total = 0.0
    for row in rows:
        blob = {}
        blob.update(row.get("commercial_data") or {})
        blob.update(row.get("packing_data") or {})
        for key in keys:
            val = blob.get(key)
            if isinstance(val, (int, float)):
                total += float(val)
                break
    return total


def run(title: str, paths: list[Path]) -> str:
    lines = ["=" * 90, title]
    existing = [(str(p), p.name) for p in paths if p.exists()]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        lines.append("MISSING: " + " | ".join(missing))
    if not existing:
        return "\n".join(lines)
    res = transform_paths(existing)
    rows = canonical_to_rows(res.items)
    lines.append(f"profile={res.profile} items={len(rows)} header={res.header}")
    for fo in res.files:
        lines.append(f"  file {fo.filename}: {fo.status} roles={fo.role_summary} msg={fo.message}")
    for w in res.warnings[:12]:
        lines.append(f"  warn: {w}")
    qty = _sum(rows, "qty", "meters")
    amount = _sum(rows, "amount")
    net = _sum(rows, "net_weight")
    gross = _sum(rows, "gross_weight")
    boxes = _sum(rows, "boxes", "rolls")
    lines.append(f"SUM qty/meters={qty:.4f} amount={amount:.4f} net={net:.4f} gross={gross:.4f} boxes/rolls={boxes:.4f}")
    for row in rows:
        art = row.get("article")
        comm = row.get("commercial_data") or {}
        pack = row.get("packing_data") or {}
        cust = row.get("customs_data") or {}
        lines.append(
            f"  {art} | qty={comm.get('qty') or pack.get('meters')} unit={comm.get('unit')} "
            f"price={comm.get('price')} amount={comm.get('amount')} "
            f"net={pack.get('net_weight')} gross={pack.get('gross_weight')} "
            f"boxes={pack.get('boxes') or pack.get('rolls')} "
            f"hs={cust.get('hs_code')} tnved={cust.get('tnved_code')} desc={str(cust.get('description') or cust.get('description_en') or '')[:40]}"
        )
    return "\n".join(lines)


def main() -> None:
    beijing_dir = next(p for p in (DOCS / "17974 Шенжень 25.06 FESU5421074 (3)").iterdir() if p.is_dir() and "сход" in p.name.lower())
    beijing_src = list(beijing_dir.glob("*003*"))
    catalog = list((DOCS / "для тест" / "BEIJING GOLDLUCK CO., LTD").glob("*сводная*"))
    kit_18080 = next(p for p in (DOCS).iterdir() if p.is_dir() and p.name.startswith("18080"))
    src_18080 = next(p for p in kit_18080.iterdir() if p.is_dir() and "сход" in p.name.lower())
    inv_621 = list(src_18080.glob("621-1*INVOICE*"))
    pl_621 = list(src_18080.glob("621-1*PL*"))
    spec_621 = list(src_18080.glob("621-1*Spec*"))
    bestway = DOCS / "для тест" / "bestway"
    blocks = [
        run("BEIJING 003 + catalog", beijing_src + catalog),
        run("18080 621-1", inv_621 + pl_621 + spec_621),
    ]
    text = "\n\n".join(blocks)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} chars={len(text)}")


if __name__ == "__main__":
    main()
