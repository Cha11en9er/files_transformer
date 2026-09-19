"""Tests for the universal transformer core, driven by the real supply files.

These read the actual documents/ tree and compare the merged result against the
customer's etalon numbers. If documents/ is not present (CI without data) the file
tests skip, but the pure-logic tests always run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.transform.canonical import ColumnStat, classify_columns, parse_number
from app.transform.extract import extract_sheet
from app.transform.merge import match_key, merge_documents
from app.transform.reader import read_workbook

DOCS = Path(__file__).resolve().parents[2] / "documents"
requires_docs = pytest.mark.skipif(not DOCS.exists(), reason="documents/ tree not available")


# --------------------------------------------------------------------------- #
# pure logic
# --------------------------------------------------------------------------- #

def test_match_key_normalizes_names():
    assert match_key("MO SHO-01") == match_key("MOSHO-01") == "MOSHO01"
    assert match_key("VA-01") == match_key("VA01") == "VA01"
    assert match_key("VA 04") == match_key("VA04") == "VA04"
    assert match_key("Noble 110") == "NOBLE110"


def test_parse_number_eu_us():
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("19,9") == 19.9
    assert parse_number("2 736.15") == 2736.15
    assert parse_number("5,78 USD") == 5.78
    assert parse_number("abc") is None


def test_classify_columns_multilingual():
    cols = [
        ColumnStat(0, "№", ["1", "2", "3"]),
        ColumnStat(1, "Art./Артикул", ["Noble 110", "Omega 03", "Solo 794"]),
        ColumnStat(2, "H.S. CODE", ["5407610000", "5407610000", "5407610000"]),
        ColumnStat(3, "ROLLS", ["15", "81", "6"]),
        ColumnStat(4, "TOTAL M2", ["868.33", "5346.5", "448.29"]),
        ColumnStat(5, "METERS", ["611.5", "3712.9", "302.9"]),
        ColumnStat(6, "UNIT PRICE(RMB)", ["43.8", "9.5", "19"]),
        ColumnStat(7, "AMOUNT(RMB)", ["48990.3", "50757.5", "12293"]),
    ]
    mapping = classify_columns(cols)
    assert mapping[1] == "article"
    assert mapping[2] == "hs_code"
    assert mapping[3] == "rolls"
    assert mapping[4] == "area"
    assert mapping[5] == "meters"
    assert mapping[6] == "price"
    assert mapping[7] == "amount"


def test_measurement_vs_amount_by_values():
    cols = [
        ColumnStat(0, "Art No.", ["MD 811", "MD 812"]),
        ColumnStat(1, "", ["32*20*10", "32*20*10"]),   # unlabeled dimension
        ColumnStat(2, "Amount (CNY)", ["2825.4", "11845.92"]),
    ]
    mapping = classify_columns(cols)
    assert mapping[1] == "measurement"
    assert mapping[2] == "amount"


# --------------------------------------------------------------------------- #
# real files
# --------------------------------------------------------------------------- #

def _load(rel: str):
    out = []
    for sheet in read_workbook(str(DOCS / rel)):
        ex = extract_sheet(sheet)
        if ex:
            out.append(ex)
    return out


def _by_key(items):
    return {it.key: it for it in items}


@requires_docs
def test_hangzhou_child_family_and_weight_split():
    base = "18080 ЛЮ 621 ТМЛ/Исходные"
    if not (DOCS / base).exists():
        base = "3_pravka/18080/Исходные"
    sheets = (
        _load(f"{base}/621-1-YS-RMB-EXW-INVOICE.XLSX")
        + _load(f"{base}/621-1-YS-RMB-EXW-PL.XLSX")
        + _load(f"{base}/621-1-YS-RMB-EXW-Specification.xls")
    )
    items = _by_key(merge_documents(sheets))

    noble110 = items[match_key("Noble 110")]
    assert noble110.get("meters") == pytest.approx(561.6, abs=0.1)
    assert noble110.get("price") == pytest.approx(43.8, abs=0.01)
    assert noble110.get("amount") == pytest.approx(24598.08, abs=1.0)
    assert noble110.get("net_weight") == pytest.approx(533.52, abs=0.5)
    # etalon G.W comes from sender-spec share of PL total (not meters share)
    assert noble110.get("gross_weight") == pytest.approx(550.36, abs=0.05)
    assert noble110.get("hs_code") == "5407610000"

    noble229 = items[match_key("Noble 229")]
    assert noble229.get("meters") == pytest.approx(173.5, abs=0.1)
    assert noble229.get("net_weight") == pytest.approx(164.83, abs=0.5)
    assert noble229.get("gross_weight") == pytest.approx(169.64, abs=0.05)

    # the two children keep separate weights that sum back to the packing family total
    assert (noble110.get("net_weight") + noble229.get("net_weight")) == pytest.approx(698.35, abs=1.0)
    assert (noble110.get("gross_weight") + noble229.get("gross_weight")) == pytest.approx(720.0, abs=0.05)


@requires_docs
def test_18080_weights_match_etalon_gross_across_kits():
    """Finished 18233 spec weights follow PL totals with sender-spec colour shares."""
    from openpyxl import load_workbook

    from app.transform.service import canonical_to_rows, transform_paths

    root = DOCS / "3_pravka" / "18080"
    if not root.exists():
        pytest.skip("3_pravka/18080 not available")

    for prefix, etalon_glob in (
        ("621-1", "*621-1*СПЕЦ*"),
        ("624-1", "*624-1*СПЕЦ*"),
    ):
        src = root / "Исходные"
        paths = [
            (str(next(src.glob(f"{prefix}*INVOICE*"))), "inv"),
            (str(next(src.glob(f"{prefix}*PL*"))), "pl"),
            (str(next(src.glob(f"{prefix}*Specification*"))), "sp"),
        ]
        rows = {
            match_key(r["article"]): r
            for r in canonical_to_rows(transform_paths(paths).items)
        }
        etalon = next((root / "Готовые").glob(etalon_glob))
        wb = load_workbook(etalon, data_only=True)
        ws = wb[wb.sheetnames[0]]
        mismatches = []
        for r in range(23, (ws.max_row or 0) + 1):
            article = ws.cell(r, 4).value
            if not article:
                continue
            en = ws.cell(r, 11).value
            eg = ws.cell(r, 12).value
            ours = rows.get(match_key(str(article)))
            if not ours or not isinstance(en, (int, float)) or not isinstance(eg, (int, float)):
                continue
            pn = ours["packing_data"].get("net_weight")
            pg = ours["packing_data"].get("gross_weight")
            if abs(float(pn or 0) - float(en)) > 0.05 or abs(float(pg or 0) - float(eg)) > 0.05:
                mismatches.append((article, pn, pg, en, eg))
        assert not mismatches, f"{prefix} weight mismatches vs etalon: {mismatches[:5]}"


@requires_docs
def test_weight_pl_vs_spec_flag_when_drift_exceeds_tolerance():
    """Synthetic: packing and sender-spec disagree by >0.2 kg -> yellow flag, PL wins."""
    from app.transform.extract import ExtractedSheet, Row

    invoice = ExtractedSheet(
        name="inv",
        source="inv.xlsx",
        role="invoice",
        mapping={},
        header_text="invoice",
        rows=[
            Row(article="Alpha 1", normalized="ALPHA1", fields={"meters": 100.0, "price": 10.0, "amount": 1000.0}, source="inv", role="invoice", item_no=1),
            Row(article="Alpha 2", normalized="ALPHA2", fields={"meters": 100.0, "price": 10.0, "amount": 1000.0}, source="inv", role="invoice", item_no=2),
        ],
    )
    packing = ExtractedSheet(
        name="pl",
        source="pl.xlsx",
        role="packing",
        mapping={},
        header_text="packing",
        rows=[
            Row(
                article="SOFA FABRIC Alpha",
                normalized="SOFAFABRICALPHA",
                fields={"net_weight": 100.0, "gross_weight": 110.0, "meters": 200.0, "rolls": 2.0},
                source="pl",
                role="packing",
                is_group=True,
            ),
        ],
    )
    spec = ExtractedSheet(
        name="sp",
        source="sp.xls",
        role="specification",
        mapping={},
        header_text="specification",
        detail=True,
        rows=[
            Row(article="Alpha 1", normalized="ALPHA1", fields={"net_weight": 40.0, "gross_weight": 44.0, "meters": 100.0}, source="sp", role="specification"),
            Row(article="Alpha 2", normalized="ALPHA2", fields={"net_weight": 40.0, "gross_weight": 44.0, "meters": 100.0}, source="sp", role="specification"),
        ],
    )
    items = _by_key(merge_documents([invoice, packing, spec]))
    a1 = items[match_key("Alpha 1")]
    a2 = items[match_key("Alpha 2")]
    # PL total 100, shared by equal spec nets -> 50/50 (not raw spec 40)
    assert a1.get("net_weight") + a2.get("net_weight") == pytest.approx(100.0, abs=0.01)
    assert a1.get("net_weight") == pytest.approx(50.0, abs=0.01)
    flags = a1.flags + a2.flags
    assert any(f.get("error_type") == "weight_pl_vs_spec" for f in flags)


@requires_docs
def test_beijing_catalog_matches_across_name_variants():
    catalog_sheets = _load("dumps/BEIJING GOLDLUCK CO., LTD/(описание )сводная.xlsx")
    catalog: dict[str, dict] = {}
    for s in catalog_sheets:
        for row in s.rows:
            k = match_key(row.article)
            if k and k not in catalog:
                catalog[k] = {
                    "customs_code": row.fields.get("customs_code") or row.fields.get("hs_code"),
                    "description": row.fields.get("description"),
                }
    assert catalog.get("MOSHO01"), "catalog must index MOSHO-01"

    sheets = _load(
        "dumps/BEIJING GOLDLUCK CO., LTD/(поступление)Invoice n Packing list（003-26E,20260703)-00.xlsx"
    )
    items = _by_key(merge_documents(sheets, catalog=catalog))

    md812 = items[match_key("MD 812")]
    assert md812.get("customs_code") == "7318230009"
    assert md812.get("amount") == pytest.approx(11845.92, abs=0.5)

    # invoice says "MO SHO-01", catalog says "MOSHO-01" -> must still match
    mosho = items[match_key("MO SHO-01")]
    assert mosho.get("customs_code") == "8302420000"


@requires_docs
def test_xls_merged_cells_are_filled():
    # Mora packing .xls uses merged pallet cells; article/color must not leak rows
    sheets = read_workbook(str(DOCS / "dumps/MORA/PACKING LIST 2026 - 69.xls"))
    ex = extract_sheet(sheets[0])
    assert ex is not None
    arts = {match_key(r.article) for r in ex.rows}
    assert match_key("RIO") in arts or match_key("OLD") in arts


def test_classify_weight_brutto_and_package_not_qty():
    cols = [
        ColumnStat(0, "Art No.", ["WAY-1", "WAY-2"]),
        ColumnStat(1, "QUANTITY", ["10", "20"]),
        ColumnStat(2, "PACKAGE", ["2", "4"]),
        ColumnStat(3, "WEIGHT NETTO", ["100", "200"]),
        ColumnStat(4, "WEIGHT BRUTTO", ["110", "220"]),
    ]
    mapping = classify_columns(cols)
    assert mapping[1] == "qty"
    assert mapping[2] == "rolls"
    assert mapping[3] == "net_weight"
    assert mapping[4] == "gross_weight"


def test_transform_merged_lots_components_and_stop_at_total(tmp_path: Path):
    """Synthetic Goldluck-style merges: lots vs components vs inherited sibling weights."""
    from openpyxl import Workbook

    from app.transform.service import canonical_to_rows, transform_paths

    path = tmp_path / "kit.xlsx"
    book = Workbook()
    invoice = book.active
    invoice.title = "Invoice + Packing list"
    invoice.append(["Commercial invoice"])
    invoice.append(["Art No.", "Color", "Quantity", "Unit", "Price", "Amount", "Net Wt.", "Gross Wt.", "Cartons"])
    invoice.append(["I2388-120", "Sand Black", 1080, "pcs", 5.54, 5983.2, 351, 372.5, 27])
    invoice.append([None, "Sand Black", 18, "pcs", 5.54, 99.72, 6, 6.5, 1])
    invoice.merge_cells("A3:A4")
    invoice.append(["D680", "Black", 20, "sets", 163.4, 3268, 100, 105, 4])
    invoice.append([None, None, None, None, None, None, 30, 32, 2])
    invoice.append([None, None, None, None, None, None, 33, 32, 2])
    invoice.merge_cells("A5:A7")
    invoice.merge_cells("C5:C7")
    invoice.merge_cells("E5:E7")
    invoice.merge_cells("F5:F7")
    invoice.append(["A519", "Chrome", 70, "sets", 178.2, 12474, 370, 425, 10])
    invoice.append(["A711", "Chrome", 80, "pcs", 12.5, 1000, None, None, None])
    invoice.merge_cells("G8:G9")
    invoice.merge_cells("H8:H9")
    invoice.merge_cells("I8:I9")
    invoice.append(["TOTAL", None, 1268, None, None, 22756.92, 890, 972.5, 46])
    invoice.append(["Detail packing list"])
    invoice.append(["A519-3", None, 10, None, None, None, 40, 45, 1])
    packing = book.create_sheet("Packing list")
    packing.append(["Packing list"])
    packing.append(["Art No.", "Quantity", "Cartons", "Net Wt.", "Gross Wt.", "Volume"])
    packing.append(["I2388-120", 1080, 27, 351, 372.5, 0.575])
    packing.append(["I2388-120", 18, 1, 6, 6.5, 0.021])
    packing.append(["D680", 20, 8, 163, 169, 0.56])
    packing.append(["A519", 70, 10, 370, 425, 1.2])
    packing.append(["A711", 80, 2, 12, 13, 0.1])
    packing.append(["TOTAL", 1268, 48, 902, 986, 2.456])
    book.save(path)

    rows = canonical_to_rows(transform_paths([(str(path), path.name)]).items)
    by_art: dict[str, list] = {}
    for row in rows:
        by_art.setdefault(row["article"], []).append(row)

    lots = by_art["I2388-120"]
    assert sorted(item["commercial_data"]["qty"] for item in lots) == [18, 1080]
    amounts = {item["commercial_data"]["qty"]: item["commercial_data"]["amount"] for item in lots}
    assert amounts[1080] == pytest.approx(5983.2)
    assert amounts[18] == pytest.approx(99.72)
    gross = {item["commercial_data"]["qty"]: item["packing_data"]["gross_weight"] for item in lots}
    assert gross[1080] == pytest.approx(372.5)
    assert gross[18] == pytest.approx(6.5)

    d680 = by_art["D680"]
    assert len(d680) == 1
    assert d680[0]["commercial_data"]["qty"] == 20
    assert d680[0]["commercial_data"]["amount"] == pytest.approx(3268)
    assert d680[0]["packing_data"]["net_weight"] == pytest.approx(163)
    assert d680[0]["packing_data"]["gross_weight"] == pytest.approx(169)

    a519 = by_art["A519"][0]
    assert a519["commercial_data"]["qty"] == 70
    assert a519["packing_data"]["gross_weight"] == pytest.approx(425)
    a711 = by_art["A711"][0]
    assert a711["commercial_data"]["qty"] == 80
    assert a711["packing_data"].get("gross_weight") == pytest.approx(13)
    assert not any(row["article"] == "A519-3" for row in rows)
    assert not any(str(row["article"]).upper().startswith("TOTAL") for row in rows)

