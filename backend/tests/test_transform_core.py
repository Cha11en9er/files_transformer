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


def test_classify_stacked_weight_and_series_art_headers():
    cols = [
        ColumnStat(0, "№", ["1", "2"]),
        ColumnStat(1, "CODE", ["9506620000", "9506290000"]),
        ColumnStat(2, "DESCRIPTION", ["Water ball", "Life jacket"]),
        ColumnStat(3, "COUNTRY OF ORIGIN", ["CN", "CN"]),
        ColumnStat(4, "MODEL / SERIES / ART.", ["31021", "32034"]),
        ColumnStat(5, "MANUFACTURER / BRAND", ["Bestway", "Bestway"]),
        ColumnStat(6, "WEIGHT NETTO, kg", ["35.20", "48.70"]),
        ColumnStat(7, "WEIGHT NETTO WITH PRIMARY PACKAGING, kg", ["35.20", "48.70"]),
        ColumnStat(8, "QTY", ["360", "168"]),
        ColumnStat(9, "PRICE PER USD", ["0.1304", "1.3056"]),
        ColumnStat(10, "AMOUNT, USD", ["46.94", "219.34"]),
    ]
    mapping = classify_columns(cols)
    assert mapping[4] == "article"
    assert mapping[8] == "qty"
    assert mapping[9] == "price"
    assert mapping[10] == "amount"
    assert mapping.get(6) == "net_weight" or mapping.get(7) == "net_weight"


def test_broken_and_stacked_headers_still_map():
    cols = [
        ColumnStat(0, "PACKAG E", ["21", "39"]),
        ColumnStat(1, "QTY", ["2800", "15500"]),
        ColumnStat(2, "WEIGHT NETTO, kg", ["300.90", "588.80"]),
        ColumnStat(3, "n/a", ["-", "-"]),
    ]
    mapping = classify_columns(cols)
    assert mapping[0] == "rolls"
    assert mapping[1] == "qty"
    assert mapping[2] == "net_weight"


def test_tsd_export_headers_are_bilingual():
    from app.services.export_tsd import invoice_headers, packing_headers, spec_headers

    inv = invoice_headers("USD")
    pak = packing_headers()
    spec = spec_headers("USD")
    assert any("DESCRIPTION" in h and "Описание" in h for h in inv)
    assert any("COUNTRY OF ORIGIN" in h and "Страна" in h for h in inv)
    assert any("PACKAGE" in h and "Места" in h for h in pak)
    assert any("НАИМЕНОВАНИЕ" in h and "Description" in h for h in spec)
    bilingual = classify_columns(
        [
            ColumnStat(0, "№", ["1"]),
            ColumnStat(1, "CODE / Код ТН ВЭД", ["9506620000"]),
            ColumnStat(2, "DESCRIPTION / Описание", ["Water ball"]),
            ColumnStat(3, "MODEL / SERIES / ART. / Модель, серия, арт.", ["31021"]),
            ColumnStat(4, "QTY / Кол-во", ["360"]),
        ]
    )
    assert bilingual[3] == "article"
    assert bilingual[4] == "qty"


def test_placeholder_article_keeps_two_description_lots():
    """Blank / n/a / dash in MODEL is not a SKU: two own qty/amount rows stay two goods lines."""
    from app.transform.extract import extract_sheet
    from app.transform.merge import merge_documents
    from app.transform.reader import Sheet

    inv = Sheet(
        name="inv",
        source="INV.pdf",
        grid=[
            ["№", "CODE", "DESCRIPTION", "COUNTRY OF ORIGIN", "MODEL / SERIES / ART.", "QTY", "PRICE PER USD", "AMOUNT, USD"],
            ["1", "9505900000", "BALLOONS / ВОЗДУШНЫЙ ШАР", "CN", "n/a", "2800", "0.7043", "1972.04"],
            ["2", "9505900000", "BALLOONS / ВОЗДУШНЫЙ ШАР", "CN", "-", "15500", "0.2490", "3859.50"],
            ["TOTAL", None, None, None, None, "18300", None, "5831.54"],
        ],
    )
    pl = Sheet(
        name="pl",
        source="PL.pdf",
        grid=[
            ["№", "CODE", "DESCRIPTION", "MODEL / SERIES / ART.", "PACKAGE", "QTY", "WEIGHT NETTO, kg", "WEIGHT BRUTTO, kg"],
            ["1", "9505900000", "BALLOONS / ВОЗДУШНЫЙ ШАР", "-", "21", "2800", "300.90", "338.00"],
            ["2", "9505900000", "BALLOONS / ВОЗДУШНЫЙ ШАР", "-", "39", "15500", "588.80", "661.55"],
            ["TOTAL", None, None, None, "60", "18300", "889.70", "999.55"],
        ],
    )
    inv_ex = extract_sheet(inv)
    pl_ex = extract_sheet(pl)
    assert inv_ex is not None and pl_ex is not None
    items = merge_documents([inv_ex, pl_ex])
    assert len(items) == 2
    qtys = sorted(float(it.fields["qty"]) for it in items)
    assert qtys == [2800.0, 15500.0]
    assert all(it.fields.get("description") for it in items)
    assert items[0].fields.get("net_weight") or items[1].fields.get("net_weight")
    from app.transform.service import canonical_to_rows

    rows = canonical_to_rows(items)
    assert {r["article"] for r in rows} == {"-"}
    assert {r["commercial_data"]["qty"] for r in rows} == {2800.0, 15500.0}


def test_pdf_continuation_keeps_all_rows_and_skips_header_label():
    from app.transform.extract import extract_sheet
    from app.transform.merge import merge_documents
    from app.transform.pdf import flatten_header_rows, stitch_continuation_tables
    from app.transform.reader import Sheet

    header = [
        ["№", "CODE", "DESCRIPTION", "COUNTRY", "MODEL /", "MANUFACTURER /", "WEIGHT", None, "QTY", "PRICE PER", "AMOUNT"],
        [None, None, None, "OF ORIGIN", "SERIES / ART.", "BRAND", "NETTO, kg", "NETTO WITH PRIMARY PACKAGING, kg", None, "USD", "USD"],
    ]
    page1_rows = [
        ["1", "9506620000", "Water ball", "CN", "31021", "Bestway", "35.20", "35.20", "360", "0.1304", "46.94"],
        ["2", "9506290000", "Life jacket", "CN", "32034", "Bestway", "48.70", "48.70", "168", "1.3056", "219.34"],
        ["3", "9506290000", "Armbands", "CN", "32325", "Bestway", "86.70", "86.70", "672", "0.5811", "390.50"],
        ["4", "9004909000", "Goggles 3+", "CN", "21201", "Bestway", "78.30", "78.30", "1320", "0.5817", "767.84"],
        ["5", "9004909000", "Goggles 7+", "CN", "26034", "Bestway", "29.10", "29.10", "384", "0.7450", "286.08"],
        ["6", "3926909200", "Mattress single", "CN", "67000", "Bestway", "1180.10", "1180.10", "660", "3.9218", "2588.39"],
        ["7", "3926909200", "Mattress pump", "CN", "67001", "Bestway", "752.00", "752.00", "330", "4.9983", "1649.44"],
    ]
    page2_rows = [
        ["8", "3926909200", "Mattress double", "CN", "67003", "Bestway", "4890.20", "4890.20", "1335", "8.0344", "10725.92"],
        ["9", "3926909200", "Mattress double 2", "CN", "67004", "Bestway", "1130.70", "1130.70", "262", "9.4658", "2480.04"],
        ["10", "3926909200", "Mattress pillows", "CN", "67374", "Bestway", "6858.10", "6858.10", "1569", "9.5872", "15042.32"],
        ["11", "8414208000", "Hand pump", "CN", "62002", "Bestway", "185.90", "185.90", "504", "1.2643", "637.21"],
        ["12", "8414208000", "Foot pump", "CN", "62004", "Bestway", "264.70", "264.70", "504", "1.8002", "907.30"],
        ["13", "3919900000", "Repair kit", "CN", "62091", "Bestway", "21.60", "21.60", "828", "0.0289", "23.93"],
        ["TOTAL", None, None, None, None, None, "15561.30", "15561.30", "8896", None, "35765.25"],
    ]
    flat = flatten_header_rows(header + page1_rows)
    assert "SERIES / ART." in str(flat[0][4])
    assert flat[1][4] == "31021"
    pages = stitch_continuation_tables([(1, header + page1_rows), (2, page2_rows)])
    assert pages[1][1][0][4]
    sheet1 = Sheet(name="p1", grid=pages[0][1], source="INV.pdf")
    sheet2 = Sheet(name="p2", grid=pages[1][1], source="INV.pdf")
    ex1 = extract_sheet(sheet1)
    ex2 = extract_sheet(sheet2, inherited_mapping=ex1.mapping, inherited_role=ex1.role)
    assert ex1 is not None and ex2 is not None
    arts = [row.article for row in ex1.rows + ex2.rows]
    assert "SERIES / ART." not in arts
    assert arts == [
        "31021", "32034", "32325", "21201", "26034", "67000",
        "67001", "67003", "67004", "67374", "62002", "62004", "62091",
    ]
    items = merge_documents([ex1, ex2])
    assert len(items) == 13
    by_art = {it.article: it for it in items}
    assert by_art["67004"].fields["qty"] == 262
    assert by_art["62091"].fields["amount"] == pytest.approx(23.93)


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
    packing.append(["Detail packing list"])
    packing.append(["Art No.", "Color", "Quantity", "Unit", "Qty/CTN", "Cartons", "Measurement (Pallets)", "Gross Wt. (kg)", "Net Wt. (kg)", "Pallets"])
    packing.append(["A519-3", "Sand black", 70, "sets", 5, 14, "110*110*125", 182, 140, 1])
    book.save(path)

    rows = canonical_to_rows(transform_paths([(str(path), path.name)]).items)
    by_art: dict[str, list] = {}
    for row in rows:
        by_art.setdefault(row["article"], []).append(row)

    lots = by_art["I2388-120"]
    assert len(lots) == 1
    assert lots[0]["commercial_data"]["qty"] == pytest.approx(1098)
    assert lots[0]["commercial_data"]["amount"] == pytest.approx(6082.92)
    line_qty = sorted(line["qty"] for line in lots[0]["commercial_data"]["lots"])
    assert line_qty == [18, 1080]
    amounts = {line["qty"]: line["amount"] for line in lots[0]["commercial_data"]["lots"]}
    assert amounts[1080] == pytest.approx(5983.2)
    assert amounts[18] == pytest.approx(99.72)
    pack_lines = lots[0]["packing_data"]["lines"]
    gross_vals = sorted(
        float(line["gross_weight"])
        for line in pack_lines
        if isinstance(line.get("gross_weight"), (int, float))
    )
    assert gross_vals == pytest.approx([6.5, 372.5])

    d680 = by_art["D680"]
    assert len(d680) == 1
    assert d680[0]["commercial_data"]["qty"] == 20
    assert d680[0]["commercial_data"]["amount"] == pytest.approx(3268)
    assert d680[0]["packing_data"]["net_weight"] == pytest.approx(163)
    assert d680[0]["packing_data"]["gross_weight"] == pytest.approx(169)
    assert len(d680[0]["packing_data"]["lines"]) == 3

    a519 = by_art["A519"][0]
    assert a519["commercial_data"]["qty"] == 70
    assert a519["packing_data"]["gross_weight"] == pytest.approx(425)
    assert "110*110*125" in str(a519["packing_data"].get("measurement") or "")
    a711 = by_art["A711"][0]
    assert a711["commercial_data"]["qty"] == 80
    assert a711["packing_data"].get("gross_weight") == pytest.approx(13)
    assert not any(row["article"] == "A519-3" for row in rows)
    assert not any(str(row["article"]).upper().startswith("TOTAL") for row in rows)


def test_beijing_goldluck_merged_article_is_one_item(tmp_path: Path):
    kit = (
        DOCS
        / "4_pravka"
        / "для тест"
        / "BEIJING GOLDLUCK CO., LTD"
    )
    invoice = next(kit.glob("*Invoice*"), None)
    catalog = next(kit.glob("*сводная*"), None)
    if invoice is None or catalog is None:
        pytest.skip("Beijing Goldluck kit not available")

    from app.services.export_beijing import export_beijing_book
    from app.transform.service import canonical_to_rows, transform_paths

    result = transform_paths([(str(invoice), invoice.name), (str(catalog), catalog.name)])
    rows = canonical_to_rows(result.items)
    no228 = [row for row in rows if match_key(row["article"]) == match_key("NO228")]
    assert len(no228) == 1
    assert no228[0]["commercial_data"]["qty"] == pytest.approx(1500)
    lots = no228[0]["commercial_data"].get("lots") or []
    assert sorted(lot["qty"] for lot in lots) == [20, 1480]
    d680 = next(row for row in rows if match_key(row["article"]) == match_key("D680"))
    desc = " ".join(
        str(d680["customs_data"].get(k) or "")
        for k in ("description", "description_en", "description_ru")
    )
    assert "D680-1" not in desc
    assert "support" in desc.lower() or "опор" in desc.lower()
    assert not str(d680["customs_data"].get("description_ru") or "").startswith("/")
    assert not any("A519-3" in str(row["article"]) for row in rows)
    md811 = next(row for row in rows if match_key(row["article"]) == match_key("MD 811"))
    assert not (md811["customs_data"].get("tnved_code") or md811["customs_data"].get("hs_code"))
    assert any(err.get("error_type") == "catalog_not_found" for err in (md811.get("validation_errors") or []))

    out = export_beijing_book(rows, tmp_path / "beijing.xlsx", result.header)
    from openpyxl import load_workbook

    wb = load_workbook(out)
    invoice_ws = wb["Invoice"]
    numbers = []
    for row in invoice_ws.iter_rows(min_row=1, max_row=80, values_only=True):
        if isinstance(row[0], int):
            numbers.append(row[0])
    assert numbers[-1] == 34
    assert numbers == list(range(1, 35))
    spec = wb["Specification"]
    spec_nos = [row[0] for row in spec.iter_rows(min_row=1, max_row=80, values_only=True) if isinstance(row[0], int)]
    assert spec_nos[-1] == 34
    desc_ws = wb["описание"]
    desc_arts = [row[0] for row in desc_ws.iter_rows(min_row=1, max_row=80, values_only=True) if row and row[0] not in (None, "Item/ Артикул")]
    assert "A519" in desc_arts
    assert "A519-3" not in desc_arts
    packing = wb["Packing list"]
    pack_nos = [row[0] for row in packing.iter_rows(min_row=1, max_row=120, values_only=True) if isinstance(row[0], int)]
    assert pack_nos[-1] == 34
    a519_row = None
    a711_row = None
    for row in packing.iter_rows(min_row=1, max_row=80, values_only=False):
        art = row[2].value if len(row) > 2 else None
        if art == "A519":
            a519_row = row
        if art == "A711":
            a711_row = row
    assert a519_row is not None and a711_row is not None
    assert a519_row[7].value not in (None, "")
    merged_coords = {str(rng) for rng in packing.merged_cells.ranges}
    assert any("H" in coord or coord.startswith("H") for coord in merged_coords)


def test_spec_stacked_header_and_letterhead_page_are_not_goods():
    from app.transform.extract import extract_sheet, mapping_fits_sheet
    from app.transform.merge import merge_documents
    from app.transform.pdf import flatten_header_rows
    from app.transform.reader import Sheet

    header = [
        ["НАИМЕНОВАНИЕ ТОВАРА", "МОДЕЛЬ,", "ФИРМА ПРОИЗВ-ЛЬ", "СТРАНА ПРОИСХ.", "КОЛ-ВО", "ЕД.ИЗМ.", "ВЕС БРУТТО, КГ", "ВЕС НЕТТО, КГ", "СТОИМОСТЬ,", None, "СТ-СТЬ"],
        [None, "СЕРИЯ, АРТ.", None, None, None, None, None, None, "USD", "ЕД.ИЗМ.", "USD"],
    ]
    goods = [
        ["Water ball / Водный мяч", "310210", "Bestway / Bestway", "CN", "360", "ШТ", "39.10", "36.40", "0.1362", "ШТ", "49.03"],
        ["Repair kit / Набор", "62091", "Bestway / Bestway", "CN", "828", "ШТ", "23.46", "20.70", "0.0277", "ШТ", "22.94"],
        ["ИТОГО:", None, None, None, "1188", None, "62.56", "57.10", None, None, "71.97"],
    ]
    flat = flatten_header_rows(header + goods)
    assert "СЕРИЯ" in str(flat[0][1]).upper() or "АРТ" in str(flat[0][1]).upper()
    sheet = Sheet(name="p1", grid=flat, source="SPEC.pdf")
    ex = extract_sheet(sheet)
    assert ex is not None
    arts = [row.article for row in ex.rows]
    assert arts == ["310210", "62091"]
    assert ex.stopped_at_total

    footer = Sheet(
        name="p3",
        grid=[
            ["Условия поставки: F", "CA Шанхай (Инкотермс 2020)"],
            ["INN: 9726095048", "KPP: 507401001"],
            ["Current account in", "rubles: No.40702810310001872"],
            ["Генеральный дир", "ектор"],
        ],
        source="SPEC.pdf",
    )
    assert not mapping_fits_sheet(footer, ex.mapping)
    items = merge_documents([ex])
    assert [it.article for it in items] == ["310210", "62091"]


@requires_docs
def test_besway2_pdf_kit_is_fourteen_items(tmp_path: Path):
    kit = DOCS / "4_pravka" / "для тест" / "besway 2"
    pdfs = sorted(kit.glob("*.pdf"))
    if len(pdfs) < 3:
        pytest.skip("besway 2 PDFs not available")
    from app.services.export import export_beijing
    from app.transform.service import canonical_to_rows, transform_paths

    result = transform_paths([(str(p), p.name) for p in pdfs])
    assert len(result.items) == 14
    arts = [it.article for it in result.items]
    assert "СЕРИЯ, АРТ." not in arts
    assert "310210" in arts or "31021" in arts
    assert result.header.get("invoice_no") == "NH-331005"
    assert "HMK" in str(result.header.get("seller") or "").upper()
    assert "NECARGO" in str(result.header.get("buyer") or "").upper()
    contract = str(result.header.get("contract_no") or "")
    assert "NEC-01" in contract.upper().replace("С", "C") or "01/10" in contract
    assert "SHANGHAI" in str(result.header.get("delivery_terms") or "").upper()
    assert "HONGKONG" in str(result.header.get("seller_address") or "").upper()
    assert "PODOLSK" in str(result.header.get("buyer_address") or "").upper() or "142116" in str(result.header.get("buyer_address") or "")
    assert all("обработан кодом, нашлось" in (f.message or "") for f in result.files if f.status == "ok")
    assert all("14" in (f.message or "") for f in result.files if f.status == "ok")
    rows = canonical_to_rows(result.items)
    path = export_beijing(rows, tmp_path / "export.xlsx", result.header)
    assert "ТСД" in path.name or path.suffix == ".xlsx"
    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert "INV" in wb.sheetnames
    assert "PAK" in wb.sheetnames
    inv = wb["INV"]
    blob = " ".join(str(inv.cell(r, 1).value or "") for r in range(1, 16))
    assert "HMK" in blob.upper() or "INVOICE" in blob.upper()
    assert "BEIJING GOLDLUCK" not in blob.upper()


@requires_docs
def test_matrac_pdf_kit_is_tsd_not_goldluck(tmp_path: Path):
    kit = DOCS / "4_pravka" / "для тест" / "Матрац"
    pdfs = sorted(
        p for p in kit.glob("*.pdf") if p.name.upper().startswith(("INV", "PAK"))
    )
    if len(pdfs) < 2:
        pytest.skip("Матрац PDFs not available")
    from app.services.export import export_beijing
    from app.transform.service import canonical_to_rows, transform_paths

    result = transform_paths([(str(p), p.name) for p in pdfs])
    assert len(result.items) == 27
    arts = [it.article for it in result.items]
    assert "64756" in arts
    assert "58671EU" in arts
    assert result.header.get("invoice_no") == "IDV3040C"
    assert "HMK" in str(result.header.get("seller") or "").upper()
    assert "NECARGO" in str(result.header.get("buyer") or "").upper()
    assert "DAP" in str(result.header.get("delivery_terms") or "").upper()
    assert "CHERTANOVSKAYA" in str(result.header.get("buyer_address") or "").upper() or "117534" in str(result.header.get("buyer_address") or "")
    rows = canonical_to_rows(result.items)
    assert rows[0]["customs_data"].get("hs_code") or rows[0]["customs_data"].get("tnved_code")
    path = export_beijing(rows, tmp_path / "export.xlsx", result.header)
    assert "ТСД" in path.name or path.suffix == ".xlsx"
    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert "INV" in wb.sheetnames
    blob = " ".join(str(wb["INV"].cell(r, 1).value or "") for r in range(1, 18))
    assert "HMK" in blob.upper()
    assert "DAP" in blob.upper() or "DAP" in str(wb["INV"].cell(14, 1).value or "").upper() or any(
        "DAP" in str(wb["INV"].cell(r, 1).value or "").upper() for r in range(1, 18)
    )
    assert "BEIJING GOLDLUCK" not in blob.upper()
    inv_headers = " ".join(
        str(wb["INV"].cell(r, c).value or "")
        for r in range(1, 30)
        for c in range(1, 13)
    )
    assert "DESCRIPTION" in inv_headers
    assert "Описание" in inv_headers
    pak_headers = " ".join(
        str(wb["PAK"].cell(r, c).value or "")
        for r in range(1, 30)
        for c in range(1, 12)
    )
    assert "PACKAGE" in pak_headers
    assert "Места" in pak_headers


@requires_docs
def test_shary_pdf_kit_two_balloon_lots():
    kit = DOCS / "4_pravka" / "для тест" / "шары"
    pdfs = sorted(kit.glob("*.pdf"))
    if len(pdfs) < 3:
        pytest.skip("шары PDFs not available")
    from app.transform.service import canonical_to_rows, transform_paths

    result = transform_paths([(str(p), p.name) for p in pdfs])
    assert all("нашлось" in (f.message or "") for f in result.files if f.filename.lower().endswith(".pdf"))
    assert len(result.items) == 2
    rows = canonical_to_rows(result.items)
    qtys = sorted(float(r["commercial_data"]["qty"]) for r in rows)
    assert qtys == [2800.0, 15500.0]
    assert all(r["article"] == "-" for r in rows)
    assert all("ШАР" in str(r["customs_data"].get("description_ru") or r["customs_data"].get("description") or "").upper()
               or "BALLOON" in str(r["customs_data"].get("description_en") or r["customs_data"].get("description") or "").upper()
               for r in rows)
    assert rows[0]["customs_data"].get("hs_code") == "9505900000" or rows[0]["customs_data"].get("tnved_code") == "9505900000"
    nets = sorted(float(r["packing_data"].get("net_weight") or 0) for r in rows)
    assert nets == [300.9, 588.8]


