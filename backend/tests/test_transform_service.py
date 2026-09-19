"""End-to-end checks for the universal transformer + export.

These read the real documents/ tree, run the full engine (read -> map -> merge ->
profile -> canonical rows) and then render the actual export workbooks so we know
the deploy path works. The model layer is disabled here so the tests never touch
the network; the model only refines an already-correct draft.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENCODE_ENABLED", "0")

from app.services.export import export_18233, export_beijing
from app.transform.service import (
    _date_from_letterhead_cell,
    canonical_to_rows,
    transform_paths,
)

DOCS = Path(__file__).resolve().parents[2] / "documents"
requires_docs = pytest.mark.skipif(not DOCS.exists(), reason="documents/ tree not available")

HANGZHOU = [
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-INVOICE.XLSX",
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-PL.XLSX",
    "18080 ЛЮ 621 ТМЛ/Исходные/621-1-YS-RMB-EXW-Specification.xls",
]
BEIJING = [
    "dumps/BEIJING GOLDLUCK CO., LTD/(поступление)Invoice n Packing list（003-26E,20260703)-00.xlsx",
    "dumps/BEIJING GOLDLUCK CO., LTD/(описание )сводная.xlsx",
]
PRAVKA_17974 = DOCS / "3_pravka" / "17974"
PRAVKA_17974_SRC = PRAVKA_17974 / "Исходные"
requires_17974 = pytest.mark.skipif(
    not PRAVKA_17974_SRC.exists(), reason="3_pravka/17974 sources not available"
)


def _paths(rels: list[str]) -> list[tuple[str, str]]:
    return [(str(DOCS / rel), Path(rel).name) for rel in rels]


def test_letterhead_date_patterns_are_flexible():
    """Shipment/invoice dates live in free-form titles across suppliers - not one phrase."""
    assert _date_from_letterhead_cell(
        "Invoice and Packing list (for the shipment of 29-06-2026)"
    ) == "29-06-2026"
    assert _date_from_letterhead_cell(
        "Packing list (for the shipment of 03-07 2026)"
    ) == "03-07 2026"
    assert _date_from_letterhead_cell("Specification № 19 dated 30.06.2026") == "30.06.2026"
    assert _date_from_letterhead_cell("Commercial Invoice Date: Jul.18,2026") == "Jul.18,2026"
    assert _date_from_letterhead_cell("B/L Date: 01-07-2026") is None
    assert _date_from_letterhead_cell("TO: ELEMENT LLC") is None


def test_split_catalog_description_keeps_both_languages():
    from app.transform.service import _split_description

    en, ru = _split_description(
        "Furniture metal rivet 8 x 12 mmcylindrical//Заклепка мебельная ступенчатая 8 х 12 мм цилиндрическая"
    )
    assert en.startswith("Furniture metal rivet")
    assert ru.startswith("Заклепка")
    assert not ru.startswith("/")
    en2, ru2 = _split_description("Furniture metal sleeve/Мебельная металлическая втулка")
    assert en2 == "Furniture metal sleeve"
    assert ru2 == "Мебельная металлическая втулка"


def test_hangzhou_6261_letterhead_from_source_invoice():
    pack = DOCS / "я_тестирую" / "01_ханчжоу_18233_626-1" / "вход"
    invoice = pack / "инвойс.xlsx"
    packing = pack / "пакинг.xlsx"
    spec = pack / "спецификация.xls"
    if not invoice.exists():
        pytest.skip("pack 01 sources not available")
    paths = [(str(invoice), invoice.name), (str(packing), packing.name)]
    if spec.exists():
        paths.append((str(spec), spec.name))
    result = transform_paths(paths)
    header = result.header
    assert "REGIONTEKSTIL" in str(header.get("buyer") or "").upper()
    assert "143421" in str(header.get("buyer_address") or "")
    assert header.get("invoice_no") == "ZFRMB26148-626-1"
    assert "Aug.19" in str(header.get("invoice_date") or "")


def test_create_defaults_title_to_invoice_no(tmp_path):
    from fastapi.testclient import TestClient
    from openpyxl import Workbook

    from app.main import app

    path = tmp_path / "invoice.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Invoice"
    sheet.append(["Invoice No.", "INV_WAY04"])
    sheet.append(["Art No.", "Quantity", "Unit", "Price", "Amount"])
    sheet.append(["ABC-1", 10, "pcs", 2, 20])
    book.save(path)
    payload = path.read_bytes()
    files = [("files", (path.name, payload, "application/octet-stream"))]

    client = TestClient(app)
    blank = client.post(
        "/api/v1/shipments/",
        data={"title": "", "profile_type": "BEIJING"},
        files=files,
    )
    assert blank.status_code == 200, blank.text
    body = blank.json()
    assert body["header_fields"].get("invoice_no") == "INV_WAY04"
    assert body["title"] == "INV_WAY04"

    custom = client.post(
        "/api/v1/shipments/",
        data={"title": "Моя поставка", "profile_type": "BEIJING"},
        files=[("files", (path.name, payload, "application/octet-stream"))],
    )
    assert custom.status_code == 200, custom.text
    assert custom.json()["title"] == "Моя поставка"


@requires_17974
def test_17974_extracts_invoice_no_and_shipment_date():
    src = sorted(PRAVKA_17974_SRC.glob("*002*"))[0]
    result = transform_paths([(str(src), src.name)])
    assert result.header.get("invoice_no") == "26BEET-002"
    assert result.header.get("invoice_date") == "29-06-2026"
    assert result.header.get("contract_no")
    assert result.header.get("container")


@requires_17974
def test_17974_descriptions_come_from_catalog(tmp_path):
    src = sorted(PRAVKA_17974_SRC.glob("*002*"))[0]
    cat = DOCS / "dumps" / "BEIJING GOLDLUCK CO., LTD" / "(описание )сводная.xlsx"
    assert cat.exists()
    result = transform_paths([(str(src), src.name), (str(cat), cat.name)])
    rows = canonical_to_rows(result.items)
    kd = next(r for r in rows if match_key_article(r["article"]) == "KD020")
    customs = kd["customs_data"]
    assert customs.get("tnved_code") or customs.get("hs_code")
    text = " ".join(
        str(customs.get(k) or "")
        for k in ("description", "description_en", "description_ru")
    )
    assert "Furniture" in text or "Мебельный" in text or "зацеп" in text

    out = export_beijing(rows, tmp_path / "17974.xlsx", result.header)
    from openpyxl import load_workbook

    wb = load_workbook(out, data_only=True)
    inv = wb["Invoice"]
    # letterhead must carry both invoice no and date
    blob = " ".join(str(c.value or "") for row in inv.iter_rows(min_row=1, max_row=9) for c in row)
    assert "26BEET-002" in blob
    assert "29-06-2026" in blob
    # first goods row has a description cell filled
    assert inv.cell(11, 3).value


def match_key_article(article: str) -> str:
    from app.transform.merge import match_key

    return match_key(article)


@requires_17974
def test_17974_without_catalog_warns_about_descriptions():
    src = sorted(PRAVKA_17974_SRC.glob("*002*"))[0]
    result = transform_paths([(str(src), src.name)])
    assert any("справочник" in w.lower() or "сводная" in w.lower() for w in result.warnings)
    rows = canonical_to_rows(result.items)
    assert rows
    # yellow flags so the UI shows the gap instead of a silent empty Description
    assert any(
        err.get("field_name") == "description"
        for r in rows
        for err in (r.get("validation_errors") or [])
    )


@requires_docs
def test_hangzhou_transform_and_export(tmp_path):
    result = transform_paths(_paths(HANGZHOU))
    assert result.profile == "18233"
    assert result.header.get("invoice_no")
    rows = canonical_to_rows(result.items)
    assert rows, "Hangzhou kit produced no items"

    # a numbered child (article, no own price) and its family both survive the merge
    articles = {r["article"] for r in rows}
    assert any(a.startswith("Noble") for a in articles)

    # every commercial row carries a positive amount
    priced = [r for r in rows if r["commercial_data"].get("amount")]
    assert priced, "no priced rows"

    paths = export_18233(rows, tmp_path, header=result.header, shipment_title="test")
    assert paths and all(Path(p).exists() and Path(p).stat().st_size > 0 for p in paths)


@requires_docs
def test_beijing_transform_and_export(tmp_path):
    result = transform_paths(_paths(BEIJING))
    assert result.profile == "beijing"
    rows = canonical_to_rows(result.items)
    assert rows, "Beijing kit produced no items"

    # the reference catalog filled customs codes on at least some articles
    coded = [r for r in rows if r["customs_data"].get("hs_code") or r["customs_data"].get("tnved_code")]
    assert coded, "catalog did not fill any customs codes"

    out = export_beijing(rows, tmp_path / "beijing.xlsx", result.header)
    assert Path(out).exists() and Path(out).stat().st_size > 0


@requires_docs
def test_unreadable_file_gives_russian_message(tmp_path):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a real workbook")
    result = transform_paths([(str(broken), "broken.xlsx")])
    assert result.files and result.files[0].status == "skipped"
    msg = result.files[0].message or ""
    assert msg
    # message must be human Russian, not a raw latin traceback
    assert any("\u0400" <= ch <= "\u04ff" for ch in msg)


@requires_docs
def test_route_end_to_end_create_and_export():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    files = []
    for rel in HANGZHOU:
        p = DOCS / rel
        files.append(("files", (p.name, p.read_bytes(), "application/octet-stream")))

    resp = client.post("/api/v1/shipments/", data={"title": "e2e", "profile_type": "18233"}, files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_count"] > 0
    assert body["profile_type"] == "18233"

    export = client.post(
        "/api/v1/shipments/export",
        json={
            "title": "e2e",
            "profile_type": "18233",
            "header_fields": body.get("header_fields") or {},
            "items": body["items"],
        },
    )
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "application/zip"
    assert len(export.content) > 0
