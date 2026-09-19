from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.transform.extract import extract_sheet
from app.transform.merge import merge_documents, match_key
from app.transform.reader import read_workbook

DOCS = Path(__file__).resolve().parents[2] / "documents"


def load(rel: str):
    out = []
    for sheet in read_workbook(str(DOCS / rel)):
        ex = extract_sheet(sheet)
        if ex:
            out.append(ex)
    return out


def show(title: str, files: list[str], catalog=None, look=None):
    print("=" * 90)
    print(title)
    sheets = []
    for rel in files:
        try:
            sheets += load(rel)
        except Exception as exc:  # noqa: BLE001
            print("  load error", rel, exc)
    for s in sheets:
        print(f"  sheet {s.source} · {s.name}: role={s.role} detail={s.detail} rows={len(s.rows)}")
    items = merge_documents(sheets, catalog=catalog)
    print(f"  -> {len(items)} canonical items")
    for it in items:
        if look and match_key(it.article) not in {match_key(x) for x in look}:
            continue
        f = it.fields
        print(f"   {it.article:16} rolls={f.get('rolls')} m={f.get('meters')} m2={f.get('area')} "
              f"nw={f.get('net_weight')} gw={f.get('gross_weight')} price={f.get('price')} amt={f.get('amount')} "
              f"hs={f.get('hs_code')} cust={f.get('customs_code')} desc={str(f.get('description'))[:20]!r}")
        for fl in it.flags:
            print("        flag:", fl["severity"], fl["message"])


show(
    "HANGZHOU 621-1 (invoice+PL+spec)",
    [
        "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-INVOICE.XLSX",
        "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-PL.XLSX",
        "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-Specification.xls",
    ],
    look=["Noble 110", "Noble 229", "Sherlock 980", "Velvet LUX 01"],
)


# Beijing: build catalog from сводная
def build_catalog(rel: str):
    from app.transform.merge import match_key as mk
    cat = {}
    for s in load(rel):
        for row in s.rows:
            k = mk(row.article)
            if k and k not in cat:
                cat[k] = {
                    "customs_code": row.fields.get("customs_code") or row.fields.get("hs_code"),
                    "description": row.fields.get("description"),
                }
    return cat


cat = build_catalog("dumps/BEIJING GOLDLUCK CO., LTD/(описание )сводная.xlsx")
print("catalog size", len(cat), "MOSHO-01 ->", cat.get("MOSHO01"), "VA01 ->", cat.get("VA01"))
show(
    "BEIJING 003 (invoice+packing) + catalog",
    ["dumps/BEIJING GOLDLUCK CO., LTD/(поступление)Invoice n Packing list（003-26E,20260703)-00.xlsx"],
    catalog=cat,
    look=["MD 812", "MO SHO-01", "VA04", "NO228"],
)
