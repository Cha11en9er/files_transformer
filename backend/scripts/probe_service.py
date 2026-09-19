from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.transform.service import transform_paths, canonical_to_rows

DOCS = Path(__file__).resolve().parents[2] / "documents"


def run(title, rels):
    print("=" * 90)
    print(title)
    paths = [(str(DOCS / r), Path(r).name) for r in rels]
    res = transform_paths(paths)
    print("profile:", res.profile)
    print("header:", res.header)
    for fo in res.files:
        print(f"  file {fo.filename}: {fo.status} roles={fo.role_summary} msg={fo.message}")
    for w in res.warnings:
        print("  warn:", w)
    print("scanned:", res.scanned_pdfs)
    rows = canonical_to_rows(res.items)
    print(f"items={len(rows)}")
    for row in rows[:5]:
        print("   ", row["article"], "|", row["commercial_data"], "|", row["packing_data"], "|", row["customs_data"])


run("HANGZHOU 621-1 kit", [
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-INVOICE.XLSX",
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-PL.XLSX",
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-Specification.xls",
])

run("BEIJING 003 + catalog", [
    "dumps/BEIJING GOLDLUCK CO., LTD/(поступление)Invoice n Packing list（003-26E,20260703)-00.xlsx",
    "dumps/BEIJING GOLDLUCK CO., LTD/(описание )сводная.xlsx",
])

run("TOSUN spec only", [
    "dumps/Tosun/Specification TOSUN 18259.xlsx",
])

run("TOSUN CI+PL pdf (text layer)", [
    "dumps/Tosun/TOSUNOGLU SM REGIONTEKSTIL_261071_CI.pdf",
    "dumps/Tosun/TOSUNOGLU SM REGION_261071_PL.pdf",
])

run("MORA (xls packing + scan invoice pdf)", [
    "dumps/MORA/PACKING LIST 2026 - 69.xls",
    "dumps/MORA/СПЕЦИФИКАЦИЯ 18049.xlsx",
    "dumps/MORA/18049 Инвойс.pdf",
])
