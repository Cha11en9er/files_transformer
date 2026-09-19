"""Probe: run the new reader+extractor on real supply files and print the result."""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.transform.extract import extract_sheet
from app.transform.reader import read_workbook

DOCS = Path(__file__).resolve().parents[2] / "documents"

TARGETS = [
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-INVOICE.XLSX",
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-PL.XLSX",
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-Specification.xls",
    "dumps/BEIJING GOLDLUCK CO., LTD/(поступление)Invoice n Packing list（003-26E,20260703)-00.xlsx",
    "dumps/MORA/PACKING LIST 2026 - 69.xls",
    "dumps/MORA/СПЕЦИФИКАЦИЯ 18049.xlsx",
    "dumps/Tosun/Specification TOSUN 18259.xlsx",
    "dumps/BEIJING GOLDLUCK CO., LTD/(описание )сводная.xlsx",
]


def main() -> None:
    for rel in TARGETS:
        path = DOCS / rel
        print("=" * 100)
        print("FILE:", rel)
        if not path.exists():
            print("  (missing)")
            continue
        try:
            sheets = read_workbook(str(path))
        except Exception as exc:  # noqa: BLE001
            print("  read error:", exc)
            continue
        for sheet in sheets[:2]:
            ex = extract_sheet(sheet)
            if ex is None:
                print(f"  sheet '{sheet.name}': no header found ({sheet.nrows}x{sheet.ncols})")
                continue
            fields = {ex.mapping[c] for c in sorted(ex.mapping)}
            print(f"  sheet '{sheet.name}' role={ex.role} detail={ex.detail} rows={len(ex.rows)}")
            print("    mapping:", {c: ex.mapping[c] for c in sorted(ex.mapping)})
            for row in ex.rows[:6]:
                compact = {k: v for k, v in row.fields.items() if not k.startswith("_")}
                print(f"    - {row.article!r:22} no={row.item_no} grp={row.is_group} {compact}")


if __name__ == "__main__":
    main()
