"""18312 Hangzhou Element: header-aware 18233 parse and 3-file export."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook

from app.services.catalog import CatalogIndex
from app.services.export import build_export_preview, export_18233
from app.services.profile_18233 import parse_bundle, reconcile_18233
from app.services.reconcile import items_to_dicts


def _ship_18312() -> Path:
    docs = Path(__file__).resolve().parents[2] / "documents" / "поставки"
    return next(p for p in docs.iterdir() if p.is_dir() and p.name.startswith("18312"))


def _src(name_part: str) -> Path:
    folder = _ship_18312() / "исходники"
    return next(p for p in folder.iterdir() if name_part in p.name.lower() and not p.name.startswith("~"))


def test_18312_invoice_qty_is_quantity_not_price() -> None:
    invoice = _src("инвойс")
    packing = _src("упаков")
    spec = _src("специф")
    catalog_path = _src("сводная")
    bundle = parse_bundle([invoice, packing, spec, catalog_path])
    catalog = CatalogIndex.from_excel(catalog_path)
    items = reconcile_18233(bundle, catalog=catalog)
    products = [i for i in items if i.source_traces.get("invoice")]
    assert len(products) == 21
    o30 = next(i for i in products if "О-30" in (i.article or ""))
    assert abs(float(o30.commercial_data["qty"]) - 18000) < 0.01
    assert abs(float(o30.commercial_data["price"]) - 0.13) < 0.001
    assert abs(float(o30.commercial_data["amount"]) - 2340) < 0.01
    assert o30.packing_data.get("rolls") == 3
    assert abs(float(o30.packing_data.get("net_weight")) - 84) < 0.01
    md = next(i for i in products if i.article == "MD813")
    assert abs(float(md.commercial_data["qty"]) - 200100) < 0.01
    assert str(md.commercial_data.get("unit") or "").upper().startswith("P")


def test_18312_export_three_xlsx_same_articles(tmp_path: Path) -> None:
    invoice = _src("инвойс")
    packing = _src("упаков")
    spec = _src("специф")
    bundle = parse_bundle([invoice, packing, spec])
    items = items_to_dicts(reconcile_18233(bundle))
    paths = export_18233(items, tmp_path, header={"invoice_no": "ZFRMB26136-354"}, shipment_title="18312")
    assert len(paths) == 3
    assert all(p.suffix.lower() == ".xlsx" for p in paths)
    assert not list(tmp_path.glob("*.txt"))
    for path in paths:
        wb = load_workbook(path)
        assert wb.active.freeze_panes is None
        wb.close()
    preview = build_export_preview(items, profile_type="18233", header={"invoice_no": "ZFRMB26136-354"}, shipment_title="18312")
    assert len(preview["files"]) == 3
    inv_headers = preview["files"][0]["sheets"][0]["headers"]
    pack_headers = preview["files"][1]["sheets"][0]["headers"]
    spec_headers = preview["files"][2]["sheets"][0]["headers"]
    inv_rows = preview["files"][0]["sheets"][0]["rows"]
    pack_rows = preview["files"][1]["sheets"][0]["rows"]
    spec_rows = preview["files"][2]["sheets"][0]["rows"]
    assert inv_headers[1] == "DESIGN"
    assert "PACKAGES" in inv_headers[3].upper()
    assert pack_headers[1] == "DESIGN"
    assert "Art." in spec_headers[2] or "Артикул" in spec_headers[2]
    product_inv = [row for row in inv_rows if isinstance(row[0], (int, float))]
    spec_products = [row for row in spec_rows if isinstance(row[0], (int, float))]
    assert len(product_inv) == len(spec_products) == 21
    assert type(product_inv[0][0]) is int
    assert type(spec_products[0][0]) is int
    assert any(str(row[1] or "").upper().startswith("TOTAL") for row in inv_rows)
    assert str(pack_rows[-1][0] or "").upper() == "TOTAL"
    assert "О-30" in str(product_inv[0][1])
    assert product_inv[0][4] == 18000 or product_inv[0][4] == 18000.0
    pack_blob = " ".join(str(row[1] or "") for row in pack_rows)
    assert "О-30" in pack_blob
    assert spec_products[0][2] == product_inv[0][1]
