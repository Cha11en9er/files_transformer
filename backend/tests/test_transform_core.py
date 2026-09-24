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


def test_split_design_keeps_category_and_sku():
    from app.transform.extract import split_design

    assert split_design("Мебельный профиль\nПрофиль О-30 (круглый, 5015)") == (
        "Мебельный профиль",
        "Профиль О-30 (круглый, 5015)",
    )
    assert split_design("SOFA FABRIC / Noble") == ("SOFA FABRIC", "Noble")
    assert split_design("MD813") == ("", "MD813")


def test_two_family_rows_keep_their_own_weights():
    """Two packing rows of one family match colour lines by metres, not by dumping both onto all."""
    from app.transform.extract import ExtractedSheet, Row

    invoice = ExtractedSheet(
        name="inv", source="inv.xlsx", role="invoice", mapping={}, header_text="invoice",
        rows=[
            Row(article="Galo 994", normalized="GALO994", fields={"meters": 427.1, "rolls": 8, "price": 19.3, "amount": 8243.03}, source="inv", role="invoice", item_no=1),
            Row(article="Galo 999", normalized="GALO999", fields={"meters": 301.5, "rolls": 6, "price": 20.1, "amount": 6060.15}, source="inv", role="invoice", item_no=2),
        ],
    )
    packing = ExtractedSheet(
        name="pl", source="pl.xlsx", role="packing", mapping={}, header_text="packing",
        rows=[
            Row(article="SOFA FABRIC\nGalo", normalized="SOFAFABRICGALO", fields={"meters": 427.1, "rolls": 8, "net_weight": 136.67, "gross_weight": 145.0}, source="pl", role="packing"),
            Row(article="SOFA FABRIC\nGalo", normalized="SOFAFABRICGALO", fields={"meters": 301.5, "rolls": 6, "net_weight": 96.48, "gross_weight": 102.0}, source="pl", role="packing"),
        ],
    )
    items = merge_documents([invoice, packing])
    by = {it.article: it for it in items}
    assert set(by) == {"Galo 994", "Galo 999"}
    assert by["Galo 994"].get("net_weight") == pytest.approx(136.67)
    assert by["Galo 999"].get("net_weight") == pytest.approx(96.48)
    assert by["Galo 994"].get("gross_weight") == pytest.approx(145.0)
    assert by["Galo 999"].get("gross_weight") == pytest.approx(102.0)


def test_longer_colour_name_absorbs_family_packing_row():
    from app.transform.extract import ExtractedSheet, Row

    invoice = ExtractedSheet(
        name="inv", source="inv.xlsx", role="invoice", mapping={}, header_text="invoice",
        rows=[
            Row(article="Marseille Linen", normalized="MARSEILLELINEN", fields={"meters": 708.4, "rolls": 14, "price": 21.5, "amount": 15230.6}, source="inv", role="invoice", item_no=1),
        ],
    )
    packing = ExtractedSheet(
        name="pl", source="pl.xlsx", role="packing", mapping={}, header_text="packing",
        rows=[
            Row(article="SOFA FABRIC\nMarseille", normalized="SOFAFABRICMARSEILLE", fields={"meters": 708.4, "rolls": 14, "net_weight": 446.29, "gross_weight": 463.0}, source="pl", role="packing"),
        ],
    )
    items = merge_documents([invoice, packing])
    assert [it.article for it in items] == ["Marseille Linen"]
    assert items[0].get("net_weight") == pytest.approx(446.29)


def test_unnumbered_category_row_is_caption_not_a_second_item():
    from app.transform.extract import extract_sheet
    from app.transform.reader import Sheet

    grid = [
        ["NO.", "DESIGN", "H.S. CODE", "PACKAGES", "QUANTITY", "UNIT M/PC", "UNIT PRICE(RMB)", "AMOUNT(RMB)"],
        [1, "Профиль О-30 (круглый, 5015)", "3926909090", 3, 18000, "M", 0.13, 2340],
        [None, "Мебельный профиль\nПрофиль О-30 (круглый, 5015)", None, 3, 18000, None, None, 2340],
        [2, "MD813", "7318230000", 1, 200100, "PC", 0.049, 9804.9],
        [None, "мебельная фурнитураMD813", None, 1, 200100, None, None, 9804.9],
        [None, "TOTAL:", None, 4, None, None, None, 12144.9],
    ]
    sheet = Sheet(name="Sheet1", grid=grid, source="invoice.xlsx")
    extracted = extract_sheet(sheet)
    assert extracted is not None
    assert len(extracted.rows) == 2
    profile = extracted.rows[0]
    rivet = extracted.rows[1]
    assert "О-30" in profile.article
    assert "мебельн" not in profile.article.lower() or "профиль о-30" in profile.article.lower()
    assert profile.fields.get("_group") == "Мебельный профиль"
    assert profile.fields.get("qty") == 18000
    assert profile.fields.get("amount") == 2340
    assert rivet.article == "MD813"
    assert rivet.fields.get("_group") == "мебельная фурнитура"
    assert rivet.fields.get("qty") == 200100


def test_packing_category_line_matches_invoice_sku():
    from app.transform.extract import ExtractedSheet, Row

    invoice = ExtractedSheet(
        name="inv",
        source="inv.xlsx",
        role="invoice",
        mapping={},
        header_text="invoice",
        rows=[
            Row(
                article="Профиль О-30 (круглый, 5015)",
                normalized="ПРОФИЛЬО-30(КРУГЛЫЙ,5015)",
                fields={"qty": 18000.0, "price": 0.13, "amount": 2340.0, "rolls": 3.0, "unit": "M", "_group": "Мебельный профиль"},
                source="inv",
                role="invoice",
                item_no=1,
            ),
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
                article="Мебельный профиль\nПрофиль О-30 (круглый, 5015)",
                normalized="МЕБЕЛЬНЫЙПРОФИЛЬПРОФИЛЬО-30(КРУГЛЫЙ,5015)",
                fields={"qty": 18000.0, "rolls": 3.0, "net_weight": 84.0, "gross_weight": 90.0, "unit": "M"},
                source="pl",
                role="packing",
                item_no=1,
            ),
        ],
    )
    items = merge_documents([invoice, packing])
    assert len(items) == 1
    item = items[0]
    assert "О-30" in item.article
    assert item.fields.get("net_weight") == 84.0
    assert item.fields.get("qty") == 18000.0
    assert item.fields.get("_group") == "Мебельный профиль"


def test_parse_number_eu_us():
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("19,9") == 19.9
    assert parse_number("2 736.15") == 2736.15
    assert parse_number("5,78 USD") == 5.78
    assert parse_number("abc") is None
    # Three fractional digits are decimals (kg/meters), not EU thousands.
    assert parse_number("77.700") == 77.7
    assert parse_number("3,047.000") == 3047.0
    # True EU thousands need 2+ groups or a comma decimal.
    assert parse_number("1.234.567") == 1234567.0
    assert parse_number("1.234,56") == 1234.56


def test_classify_bare_kg_and_pallet_headers():
    cols = [
        ColumnStat(0, "PALLET", [1, 1, 2]),
        ColumnStat(1, "ARTICLE", ["RIO", "RIO", "OLD"]),
        ColumnStat(2, "COLOUR", ["NATURAL", "DESERT", "CUOIO"]),
        ColumnStat(3, "HIDES", [74, 75, 48]),
        ColumnStat(4, "m2", [331.1, 320.52, 200.14]),
        ColumnStat(5, "kg", [235, 228, 142]),
        ColumnStat(6, "Euro/m2", [19.9, 19.9, 22.4]),
    ]
    mapping = classify_columns(cols)
    assert mapping[0] == "rolls"
    assert mapping[1] == "article"
    assert mapping[2] == "color"
    assert mapping[3] == "qty"
    assert mapping[4] == "area"
    assert mapping[5] == "net_weight"
    assert mapping[6] == "price"


def test_priced_repeating_articles_stay_packing_not_detail():
    from app.transform.reader import Sheet

    grid = [
        ["PACKING LIST Invoice n. 69"],
        ["PALLET", "ARTICLE", "COLOUR", "HIDES", "m2", "kg", "Euro/m2"],
        [1, "RIO", "NATURAL 1", 74, 331.1, 235.0, 19.9],
        [1, "RIO", "NATURAL 2", 1, 4.01, 3.0, 19.75],
        [1, "RIO", "DESERT", 75, 320.52, 228.0, 19.9],
        [1, "OLD", "CUOIO", 48, 200.14, 142.0, 22.4],
        [1, "OLD", "BEIGE", 34, 152.87, 108.0, 22.4],
        [1, "OLD", "TAUPE", 44, 200.67, 142.0, 22.4],
    ]
    sheet = Sheet(name="Foglio1", grid=grid, source="пакинг.xls")
    extracted = extract_sheet(sheet)
    assert extracted is not None
    assert extracted.detail is False
    assert extracted.role == "packing"
    assert len(extracted.rows) >= 6
    first = extracted.rows[0]
    assert first.fields.get("qty") == 74
    assert first.fields.get("area") == 331.1
    assert first.fields.get("net_weight") == 235.0
    assert first.fields.get("price") == 19.9
    assert first.fields.get("rolls") == 1
    assert first.fields.get("amount") == round(19.9 * 331.1, 2)


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
    base = "я_тестирую/03_ханчжоу_18080_621-1/вход"
    if not (DOCS / base).exists():
        base = "18080 ЛЮ 621 ТМЛ/Исходные"
    if not (DOCS / base).exists():
        base = "3_pravka/18080/Исходные"
    if not (DOCS / base).exists():
        pytest.skip("Hangzhou 621-1 sources not available")
    if "вход" in base:
        sheets = (
            _load(f"{base}/инвойс.xlsx")
            + _load(f"{base}/пакинг.xlsx")
            + _load(f"{base}/спецификация.xls")
        )
    else:
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
    assert "BESTWAY" in str(result.header.get("manufacturer") or "").upper()
    assert "HMK" not in str(result.header.get("manufacturer") or "").upper()
    assert str(result.header.get("currency") or "").upper() == "USD"
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
    inv_headers = " ".join(
        str(inv.cell(r, c).value or "")
        for r in range(1, 30)
        for c in range(1, 13)
    )
    assert "USD" in inv_headers
    assert "CNY" not in inv_headers and "RMB" not in inv_headers
    mfr_cells = [
        str(inv.cell(r, 6).value or "")
        for r in range(1, min(40, inv.max_row + 1))
    ]
    assert any("BESTWAY" in cell.upper() for cell in mfr_cells)


@requires_docs
def test_matrac_pdf_kit_is_tsd_not_goldluck(tmp_path: Path):
    kit = DOCS / "4_pravka" / "для тест" / "Матрац"
    pdfs = sorted(
        p for p in kit.glob("*.pdf") if p.name.upper().startswith(("INV", "PAK"))
    )
    if len(pdfs) < 2:
        pytest.skip("Матрац PDFs not available")
    from app.services.export import export_18233, export_beijing
    from app.transform.service import canonical_to_rows, transform_paths

    result = transform_paths([(str(p), p.name) for p in pdfs])
    assert len(result.items) == 27
    arts = [it.article for it in result.items]
    assert "64756" in arts
    assert "58671EU" in arts
    assert result.header.get("invoice_no") == "IDV3040C"
    assert "HMK" in str(result.header.get("seller") or "").upper()
    assert "INTEX" in str(result.header.get("manufacturer") or "").upper()
    assert "HMK" not in str(result.header.get("manufacturer") or "").upper()
    assert str(result.header.get("currency") or "").upper() == "USD"
    assert "NECARGO" in str(result.header.get("buyer") or "").upper()
    assert "DAP" in str(result.header.get("delivery_terms") or "").upper()
    assert "CHERTANOVSKAYA" in str(result.header.get("buyer_address") or "").upper() or "117534" in str(result.header.get("buyer_address") or "")
    rows = canonical_to_rows(result.items)
    assert rows[0]["customs_data"].get("hs_code") or rows[0]["customs_data"].get("tnved_code")
    # Poisoned seller-copy is still repaired; a real operator edit must win.
    poisoned = {
        **result.header,
        "manufacturer": result.header.get("seller"),
        "currency": None,
    }
    path = export_beijing(rows, tmp_path / "export.xlsx", poisoned)
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
    assert "USD" in inv_headers
    assert "RMB" not in inv_headers and "CNY" not in inv_headers
    assert any(
        "INTEX" in str(wb["INV"].cell(r, 6).value or "").upper()
        for r in range(1, min(40, wb["INV"].max_row + 1))
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

    edited = {
        **result.header,
        "manufacturer": "CUSTOM MAKER LLC / TESTBRAND",
        "payment_terms": "Net 90 days after clearance",
    }
    edited_path = export_beijing(rows, tmp_path / "edited.xlsx", edited)
    wb_edit = load_workbook(edited_path)
    inv_blob = " ".join(
        str(wb_edit["INV"].cell(r, c).value or "")
        for r in range(1, min(40, wb_edit["INV"].max_row + 1))
        for c in range(1, 13)
    )
    assert "CUSTOM MAKER" in inv_blob.upper()
    assert "NET 90" in inv_blob.upper()
    assert any(
        "CUSTOM MAKER" in str(wb_edit["INV"].cell(r, 6).value or "").upper()
        for r in range(1, min(40, wb_edit["INV"].max_row + 1))
    )

    # Same goods under the three-Excel profile must keep USD / Intex, not hardcoded RMB / seller.
    paths_18233 = export_18233(rows, tmp_path / "18233", poisoned, shipment_title="matrac")
    inv_18233 = next(p for p in paths_18233 if "ИНВОЙС" in p.name.upper() or "INVOICE" in p.name.upper() or "инвойс" in p.name.lower())
    wb2 = load_workbook(inv_18233)
    ws2 = wb2.active
    hdr_blob = " ".join(
        str(ws2.cell(r, c).value or "")
        for r in range(1, 40)
        for c in range(1, 14)
    )
    assert "USD" in hdr_blob.upper()
    assert "UNIT PRICE(RMB)" not in hdr_blob.upper()
    assert "AMOUNT(RMB)" not in hdr_blob.upper()
    assert "INTEX" in hdr_blob.upper()


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


# --------------------------------------------------------------------------- #
# 5_pravka-class layouts: junk rows, dotted HS, dual DESIGN, currency, blob PDF
# --------------------------------------------------------------------------- #

def test_hs_digits_dotted_float_and_not_money():
    from app.transform.canonical import hs_digits, looks_like_hs_code, parse_number, tnved_digits

    assert hs_digits("54.07.73.00.90.11") == "540773009011"
    assert hs_digits("59.03.10.90.10.00") == "590310901000"
    assert hs_digits("HS CODE :540783009011") == "540783009011"
    assert hs_digits(5903202000999.0) == "5903202000999"
    assert hs_digits("5903202000999.0") == "5903202000999"
    assert hs_digits("20270.25") is None
    assert hs_digits("1.38") is None
    assert hs_digits("2604") is None
    assert not looks_like_hs_code("44.55")
    assert tnved_digits("54.07.73.00.90.11") == "5407730090"
    assert parse_number("54.07.73.00.90.11") is None
    assert parse_number("1.234,56") == 1234.56


def test_parse_number_other_currencies():
    from app.transform.canonical import parse_number, normalize_currency_iso

    assert parse_number("5,78 USD") == 5.78
    assert parse_number("£12.50") == 12.50
    assert parse_number("3.85$") == 3.85
    assert parse_number("10061.93 TRY") == 10061.93
    assert parse_number("¥44.55") == 44.55
    assert parse_number("1.38 M") == 1.38
    assert normalize_currency_iso("PRICE PER GBP") == "GBP"
    assert normalize_currency_iso("Amount (AED)") == "AED"
    assert normalize_currency_iso("KWD") == "KWD"
    assert normalize_currency_iso("IQD") == "IQD"
    assert normalize_currency_iso("фунт стерлингов") == "GBP"
    assert normalize_currency_iso("лира") == "TRY"
    assert normalize_currency_iso("динар") is None
    assert normalize_currency_iso("USD and TRY on the same line") is None
    assert normalize_currency_iso("yuan, US dollars") is None


def test_dual_desing_metrs_unit_pice_and_junk_po_row():
    from app.transform.extract import extract_sheet
    from app.transform.reader import Sheet

    grid = [
        ["NO", "DESING", "DESING", "ROLL", "WIDTH", "METRS", "UNIT MT / PICE", "UNIT PICE", "AMOUNT", "CUSTOMS CODE"],
        [None, None, None, None, None, None, None, None, None, 2604],
        [1, "MAXWELL", 997, 13, "1.38 M", 455, "M", "¥44.55", "¥20270.25", 5903202000999.0],
        [2, "MAXWELL", 236, 20, "1.38 M", 1111, "M", "¥44.55", 49545.05, "5903202000999"],
        [None, "page 2", None, None, None, None, None, None, None, None],
        [3, "MAXWELL", 960, 8, "1.38 M", 485, "M", 44.55, 21606.75, "5903202000999"],
        [None, "TOTAL", None, 41, None, 2051, None, None, 91422.05, None],
    ]
    sheet = Sheet(name="Invoice", grid=grid, source="shipping.xls")
    extracted = extract_sheet(sheet)
    assert extracted is not None
    arts = [row.article for row in extracted.rows]
    assert arts == ["MAXWELL", "MAXWELL", "MAXWELL"]
    assert "2604" not in arts
    first = extracted.rows[0]
    assert first.fields.get("color") in (997, 997.0, "997")
    assert first.fields.get("meters") == 455
    assert first.fields.get("price") == pytest.approx(44.55, abs=0.01)
    assert first.fields.get("unit") in ("M", "m")
    assert str(first.fields.get("hs_code") or first.fields.get("customs_code")).startswith("5903202000")
    assert first.fields.get("area") == pytest.approx(455 * 1.38, abs=0.05)


def test_two_row_en_tr_header_amount_m_is_meters():
    from app.transform.extract import extract_sheet
    from app.transform.reader import Sheet

    grid = [
        ["PACKING LIST / CEKI LISTESI"],
        ["ROLL NR", "DESING NAME", "COLOR NR", "AMOUNT (M)", "NETT KG", "WIDTH", "M2"],
        ["Sack nr", "DESEN ADI", "RENK NO", "AMOUNT (M)", "BRUTT", "EN, M", "M2"],
        [1, "LORENSA", "01", "294,70", 120.5, 1.40, 412.58],
        [2, "LORENSA", "02", "50,20", 22.1, 1.40, 70.28],
        ["GENEL TOPLAM", None, None, "344,90", 142.6, None, 482.86],
    ]
    sheet = Sheet(name="Ceki", grid=grid, source="pack.xlsx")
    extracted = extract_sheet(sheet)
    assert extracted is not None
    assert len(extracted.rows) == 2
    row = extracted.rows[0]
    assert "LORENSA" in row.article.upper()
    assert row.fields.get("meters") == pytest.approx(294.70, abs=0.01)
    assert row.fields.get("amount") in (None, "") or row.fields.get("amount") != pytest.approx(294.70, abs=0.01)
    assert row.fields.get("net_weight") == pytest.approx(120.5, abs=0.05)


def test_qcreport_sheet_is_not_goods():
    from app.transform.extract import extract_sheet
    from app.transform.reader import Sheet

    grid = [
        ["Roll#", "SKU", "colour"],
        [2604, 2604, 2604],
        [1, "MAXWELL", 997],
    ]
    sheet = Sheet(name="QCReport", grid=grid, source="shipping.xls")
    assert extract_sheet(sheet) is None


def test_catalog_qty_one_does_not_overwrite_meters():
    from app.transform.extract import ExtractedSheet, Row

    invoice = ExtractedSheet(
        name="inv",
        source="inv.xlsx",
        role="invoice",
        mapping={},
        header_text="invoice USD",
        rows=[
            Row(
                article="ZIMMY",
                normalized="ZIMMY",
                fields={"meters": 322.0, "price": 5.78, "amount": 1861.16, "qty": 322.0, "width": 1.4},
                source="inv",
                role="invoice",
            ),
        ],
    )
    opis = ExtractedSheet(
        name="Опис",
        source="spec.xlsx",
        role="specification",
        mapping={},
        header_text="описание",
        rows=[
            Row(
                article="ZIMMY",
                normalized="ZIMMY",
                fields={"qty": 1.0, "meters": 55.04, "color": "925", "hs_code": "540753009011"},
                source="opis",
                role="specification",
            ),
        ],
    )
    items = merge_documents([invoice, opis])
    assert len(items) == 1
    item = items[0]
    assert item.fields.get("meters") == pytest.approx(322.0, abs=0.01)
    assert item.fields.get("color") in ("925", 925)
    assert str(item.fields.get("hs_code")).startswith("540753")
    assert item.fields.get("area") == pytest.approx(322.0 * 1.4, abs=0.05)


def test_packing_customer_name_is_article():
    from app.transform.extract import extract_sheet
    from app.transform.reader import Sheet

    grid = [
        ["Design", "Customer Name", "Meters", "N.W", "G.W"],
        ["MILL-88", "DYER 290", 622.00, 210.0, 225.0],
        ["MILL-88", "DYER 291", 400.00, 140.0, 150.0],
        ["TOTAL", None, 1022.00, 350.0, 375.0],
    ]
    sheet = Sheet(name="PL", grid=grid, source="packing.xlsx")
    extracted = extract_sheet(sheet)
    assert extracted is not None
    arts = {row.article.upper() for row in extracted.rows}
    assert "DYER 290" in arts
    assert "MILL-88" not in arts


def test_blob_pdf_letter_and_slash_lines():
    from app.transform.pdf import goods_grid_from_blob

    weavers = (
        "Desing No / Design Name / Weavers Code / Item No / PO\n"
        "D15-5745 / DYER 290 / PX03.BEJ 337 / 0 / new order 622,00 MT 3,85$ 2.394,70$\n"
        "HS CODE : 54.07.73.00.90.11\n"
        "Total Sum 622,00 MT\n"
    )
    grid = goods_grid_from_blob(weavers)
    assert len(grid) >= 2
    body = grid[1]
    assert "DYER" in str(body[0]).upper()
    assert float(body[1]) == pytest.approx(622.0, abs=0.05)
    letter = (
        "294,70 JACQUARD FLOCK PRINTED –LORENSA 6,60 1.945,02 USD\n"
        "HS CODE :540783009011\n"
        "50,20 VELVET –MILANO 7,10 356,42 USD\n"
    )
    grid2 = goods_grid_from_blob(letter)
    arts = [str(row[0]).upper() for row in grid2[1:]]
    assert any("LORENSA" in a for a in arts)


def test_canonical_to_rows_tnved_max_ten_no_dots():
    from app.transform.merge import CanonicalItem
    from app.transform.service import canonical_to_rows

    item = CanonicalItem(
        article="BLOOM",
        key="BLOOM",
        fields={"hs_code": "54.07.73.00.90.11", "meters": 10, "price": 1, "amount": 10},
    )
    rows = canonical_to_rows([item])
    hs = rows[0]["customs_data"].get("hs_code")
    tnved = rows[0]["customs_data"].get("tnved_code")
    assert hs and "." not in str(hs)
    assert tnved and "." not in str(tnved)
    assert len(str(tnved)) <= 10
    assert len(str(hs)) == 12
    assert str(tnved) == "5407730090"


def test_tr_subheader_does_not_steal_meters_or_serials():
    """EN header + TR translation + Total Roll subtotal + 10-digit roll ids."""
    from app.transform.extract import extract_sheet
    from app.transform.reader import Sheet

    grid = [
        ["PACKING LIST / CEKI LISTESI"],
        ["ROLL NR", "DESING NAME", "COLOR NR", "AMOUNT (M)", "AMOUNT (M)", "WIDTH", "m2", "NETT KG", "GROSS KG"],
        ["Sack nr", "DESEN ADI", "RENK NO", "NETT", "BRUTT", "EN", "M2", "NET KG", "BRUT KG"],
        [2026002135, "VELA", "1", 50.7, 50.7, 140, 70.98, 21.5, 21.6],
        [2026002136, "VELA", "1", 40.0, 40.0, 140, 56.0, 18.0, 18.2],
        ["-", "VELA", "Total Roll :", 2, 90.7, 90.7, "-", 126.98, 39.5, 39.8],
        [2026002140, "VELA CORD", "4", 25.0, 25.0, 140, 35.0, 12.4, 12.5],
    ]
    # The subtotal row above is misaligned vs the header on purpose: "Total Roll :"
    # sits in a middle cell, the way real packing lists print a group total.
    sheet = Sheet(name="Ceki", grid=grid, source="pack.xlsx")
    extracted = extract_sheet(sheet)
    assert extracted is not None
    assert len(extracted.rows) == 3
    vela = [row for row in extracted.rows if row.article == "VELA"]
    assert len(vela) == 2
    assert vela[0].fields.get("meters") == pytest.approx(50.7, abs=0.01)
    assert vela[0].fields.get("net_weight") == pytest.approx(21.5, abs=0.05)
    assert vela[0].fields.get("gross_weight") == pytest.approx(21.6, abs=0.05)
    assert vela[0].fields.get("width") == pytest.approx(1.4, abs=0.01)
    assert "customs_code" not in vela[0].fields
    assert "hs_code" not in vela[0].fields
    assert vela[0].fields.get("rolls") in (None, "")
    summed = sum(row.fields["meters"] for row in vela)
    assert summed == pytest.approx(90.7, abs=0.05)


def test_color_lots_stay_separate_rows():
    from app.transform.extract import extract_sheet
    from app.transform.merge import merge_documents
    from app.transform.reader import Sheet

    grid = [
        ["Customs Code", "DESING", "COLOUR", "ROLL", "WIDTH", "METRS", "UNIT", "UNIT PICE", "AMOUNT"],
        ["5903202000999", "LEDER", 11, 13, "1.38 M", 455, "M", 44.55, 20270.25],
        ["5903202000999", "LEDER", 22, 32, "1.38 M", 1111, "M", 44.55, 49495.05],
        ["5903202000999", "LEDER", 33, 8, "1.38 M", 268, "M", 44.55, 11939.40],
    ]
    sheet = Sheet(name="invoice", grid=grid, source="shipping.xls")
    extracted = extract_sheet(sheet)
    assert extracted is not None
    assert extracted.detail is False
    assert len(extracted.rows) == 3
    items = merge_documents([extracted])
    assert len(items) == 3
    meters = sorted(it.fields["meters"] for it in items)
    assert meters == [268, 455, 1111]
    assert {str(it.fields.get("color")) for it in items} == {"11", "22", "33"}


def test_design_plus_color_code_does_not_duplicate():
    from app.transform.extract import ExtractedSheet, Row
    from app.transform.merge import merge_documents

    goods = ExtractedSheet(
        name="spec",
        source="spec.xlsx",
        role="specification",
        mapping={},
        header_text="specification",
        rows=[
            Row(article="Vela 01", normalized="VELA01", fields={"meters": 90.7, "price": 6.6, "amount": 598.62, "rolls": 2, "net_weight": 39.5, "gross_weight": 39.8, "width": 1.4}, source="spec", role="specification"),
            Row(article="Vela Cord 04", normalized="VELACORD04", fields={"meters": 25.0, "price": 6.6, "amount": 165.0, "rolls": 1, "net_weight": 12.4, "gross_weight": 12.5, "width": 1.4}, source="spec", role="specification"),
        ],
    )
    packing = ExtractedSheet(
        name="Ceki",
        source="pack.xlsx",
        role="specification",
        mapping={},
        header_text="ceki",
        detail=True,
        rows=[
            Row(article="VELA", normalized="VELA", fields={"meters": 50.7, "net_weight": 21.5, "gross_weight": 21.6, "area": 70.98}, source="pack", role="specification"),
            Row(article="VELA", normalized="VELA", fields={"meters": 40.0, "net_weight": 18.0, "gross_weight": 18.2, "area": 56.0}, source="pack", role="specification"),
            Row(article="VELA CORD", normalized="VELACORD", fields={"meters": 25.0, "net_weight": 12.4, "gross_weight": 12.5, "area": 35.0}, source="pack", role="specification"),
        ],
    )
    items = merge_documents([goods, packing])
    assert len(items) == 2
    by = {it.article: it for it in items}
    assert by["Vela 01"].fields["meters"] == pytest.approx(90.7)
    assert by["Vela 01"].fields["net_weight"] == pytest.approx(39.5)
    assert by["Vela Cord 04"].fields["meters"] == pytest.approx(25.0)
    assert "VELA" not in by


def test_description_leading_sku_is_the_article():
    from app.transform.extract import extract_sheet
    from app.transform.reader import Sheet

    grid = [
        ["Описание", "Метры", "Цена", "Сумма"],
        ["MAXWELL 997 Artificial upholstery leather, made from polyurethane", 455, 44.55, 20270.25],
        ["MAXWELL 236 Artificial upholstery leather, made from polyurethane", 1111, 44.55, 49495.05],
    ]
    extracted = extract_sheet(Sheet(name="spec", grid=grid, source="spec.xlsx"))
    assert extracted is not None
    assert [row.article for row in extracted.rows] == ["MAXWELL", "MAXWELL"]
    assert extracted.rows[0].fields.get("color") == "997"
    assert "sku_missing" not in extracted.rows[0].fields
    assert "Artificial" in str(extracted.rows[0].fields.get("description"))



