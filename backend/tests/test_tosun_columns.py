from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.parsing.excel_reader import _pick_article
from app.services.field_map import classify_header, map_row


def test_tosun_headers_map_article_not_description() -> None:
    raw = {
        "product name / наименование товара": "ТКАНИ МЕБЕЛЬНЫЕ, COMPOSITION 100% OLEFIN",
        "articul/артикул": "SINDRI 162",
        "number of rolls/количество рулонов/штук": 1,
        "q-ty meters / кол-во погонных метров": 40.43,
        "price per meter $/ цена за пог. метр, долл. сша": 6.17,
        "total price, $ / цена, долл. сша": 249.45,
        "widht, m / ширина, м": 1.4,
    }
    article, _model = _pick_article(raw)
    assert article == "SINDRI 162"
    mapped = map_row(raw)
    assert mapped["article"] == "SINDRI 162"
    assert mapped["price"] == 6.17
    assert mapped["amount"] == 249.45
    assert mapped["rolls"] == 1.0
    assert mapped["meters"] == 40.43
    assert "OLEFIN" in (mapped.get("description") or "")


def test_classify_articul_column() -> None:
    assert classify_header("Articul/Артикул") == "article"
    assert classify_header("Product name / Наименование товара") == "description"
    assert classify_header("Number of rolls/Количество рулонов/штук") == "rolls"


def test_skip_directors_and_keep_articles() -> None:
    from app.parsing.product_row import is_junk_text, is_product_article

    assert is_product_article("SINDRI 162") is True
    assert is_product_article("ZIMMY 925") is True
    assert is_product_article("Noble 110") is True
    assert is_product_article("Директор по продажам Mehmet") is False
    assert is_product_article("Генеральный директор Величко И.В.") is False
def test_generic_item_qty_price_table() -> None:
    from app.parsing.table_rows import lines_from_matrix

    rows = [
        ["Item", "Qty", "Unit price", "Amount"],
        ["MD 812", "200", "0.0592", "11.84"],
        ["AB-90", "10", "12.5", "125"],
        ["Sales Director John", "", "", ""],
    ]
    lines = lines_from_matrix(rows, sheet_name="inv")
    articles = {line["article"] for line in lines}
    assert "MD 812" in articles
    assert "AB-90" in articles
    assert not any("Director" in a for a in articles)


def test_unlabeled_and_turkish_tables() -> None:
    from app.parsing.table_rows import lines_from_matrix, lines_from_plaintext
    from app.services.field_map import map_row

    bare = lines_from_matrix(
        [["MD 812", 200, 0.0592, 11.84], ["AB-90", 10, 12.5, 125]],
        sheet_name="bare",
    )
    assert {line["article"] for line in bare} == {"MD 812", "AB-90"}
    mapped = map_row(bare[0]["raw"])
    assert mapped["qty"] == 200
    assert mapped["price"] == 0.0592

    turkish = lines_from_matrix(
        [
            ["Stok Kodu", "Miktar", "Birim Fiyat", "Tutar"],
            ["SINDRI 162", "40.43", "6.17", "249.45"],
        ],
        sheet_name="tr",
    )
    assert turkish[0]["article"] == "SINDRI 162"
    assert map_row(turkish[0]["raw"])["price"] == 6.17

    text_lines = lines_from_plaintext("SINDRI 162 1 40.43 6.17 249.45\nSales Director Mehmet\n")
    assert any(line["article"] == "SINDRI 162" for line in text_lines)
    assert not any("Director" in line["article"] for line in text_lines)

    from app.parsing.table_rows import lines_from_qty_price_text

    invoice_text = "ZIMMY 1.740,82 MT. 5,78 USD 287,97 TL\nSINDRI 40,43 MT. 6,17 USD 307,40 TL\n"
    priced = lines_from_qty_price_text(invoice_text)
    assert {line["article"] for line in priced} == {"ZIMMY", "SINDRI"}

    glued = lines_from_qty_price_text(
        "UPHOLSTERY FABRICZIMMY1.740,82 MT.5,78 USD287,97 TL10.061,93 USD501.307,77 TL"
        "SINDRI40,43 MT.6,17 USD307,40 TL249,45 USD12.428,30 TL"
    )
    by_art = {line["article"]: line["raw"] for line in glued}
    assert round(by_art["ZIMMY"]["qty"], 2) == 1740.82
    assert by_art["ZIMMY"]["price"] == 5.78
    assert by_art["ZIMMY"]["amount"] == 10061.93
    assert by_art["SINDRI"]["qty"] == 40.43

    weavers = lines_from_qty_price_text(
        "D15-5745 / DYER 789 / PX83.MAVI 999 / 0 / new order 04.6 / HS CODE : 540753009011206,00 MT3,85 $793,10 $"
        "Y18-8687 / LARDASO 100 / PX92.BEYAZ 256 / 0 / new order 3.7 / HS CODE : 540753009011221,00 MT3,85 $850,85 $"
    )
    names = {line["article"] for line in weavers}
    assert "DYER 789" in names
    assert "LARDASO 100" in names


def test_numeric_mill_article_is_kept() -> None:
    from app.parsing.table_rows import lines_from_matrix

    rows = [
        ["Articul/Артикул", "Q-ty meters", "Price per meter $"],
        ["7508 11 101 0801 110501", "42", "9.95"],
    ]
    lines = lines_from_matrix(rows, sheet_name="spec")
    assert lines[0]["article"] == "7508 11 101 0801 110501"


def test_desing_name_and_color_become_article() -> None:
    from app.parsing.table_rows import lines_from_matrix

    rows = [
        ["ROLL NR", "COLOR NR", "DESING NAME", "AMOUNT (M)", "WIDTH"],
        ["Sack nr", "RENK NO", "DESEN ADI", "NETT", "EN"],
        ["2026004044", "2", "LORENSA", "50", "140"],
        ["2026004051", "4", "LORENSA", "36.5", "140"],
        ["-", "-", "Total Roll :", "5", "-"],
    ]
    lines = lines_from_matrix(rows, sheet_name="ceki")
    articles = {line["article"] for line in lines}
    assert "LORENSA 02" in articles
    assert "LORENSA 04" in articles
    assert not any("Total" in a for a in articles)


def test_merged_continuation_stops_at_total() -> None:
    from app.parsing.table_rows import lines_from_matrix
    from app.services.field_map import map_row

    rows = [
        ["Art No.", "Color", "Quantity", "Unit", "Gross Wt. (kg)", "Net Wt. (kg)"],
        ["NO228", "Sand Black", 1480, "sets", 569.8, 555],
        [None, "Sand Black", 20, "sets", 10.7, 10.5],
        ["D680", "Black", 20, "sets", 33.5, 32.5],
        [None, "PC", None, None, 19.5, 18.5],
        ["TOTAL:", None, 1500, None, 600, 580],
        ["Art No.", "Color", "Quantity", "Unit", "Gross Wt. (kg)", "Net Wt. (kg)"],
        ["A519-3", "Sand black", 70, "sets", 182, 140],
    ]
    lines = lines_from_matrix(rows, sheet_name="pl")
    articles = [line["article"] for line in lines]
    assert articles == ["NO228", "NO228", "D680"]
    first = map_row(lines[0]["raw"])
    second = map_row(lines[1]["raw"])
    assert first["qty"] == 1480
    assert second["qty"] == 20
    assert round(second["net_weight"], 1) == 10.5
    d680 = map_row(lines[2]["raw"])
    assert round(d680["gross_weight"], 1) == 53.0


def test_18233_kit_names_include_mengma_and_packing_ru() -> None:
    from pathlib import Path

    from app.api.routes.shipments import (
        _classify_18233_filename,
        _looks_like_18233,
        mixed_shipment_error,
    )

    assert _classify_18233_filename("упаковочный.xlsx") == "packing"
    assert _classify_18233_filename("625-Mengma-RMB-EXW-PL.XLSX") == "packing"
    paths = [
        Path("625-Mengma-RMB-EXW-INVOICE.XLSX"),
        Path("625-Mengma-RMB-EXW-PL.XLSX"),
        Path("625-Mengma-RMB-EXW-Specification.xls"),
    ]
    assert _looks_like_18233(paths) is True
    assert mixed_shipment_error(
        ["623-1-YS-RMB-EXW-INVOICE.XLSX", "623-2-YS-RMB-EXW-INVOICE.XLSX"]
    )


def test_tosun_pdf_kit_is_not_18233_excel_bundle() -> None:
    from pathlib import Path

    from app.api.routes.shipments import _has_separate_18233_kit, _looks_like_18233

    paths = [
        Path("инвойс.pdf"),
        Path("упаковочный.pdf"),
        Path("спецификация.xlsx"),
    ]
    assert _has_separate_18233_kit(paths) is False
    assert _looks_like_18233(paths) is False


def test_labeled_turkish_packing_blocks() -> None:
    from app.parsing.table_rows import lines_from_labeled_packing
    from app.services.field_map import map_row

    text = """
Lot Sipariş No Po No Top No Net Metre Brüt Metre Brüt Kilogram Net Kilogram Net Adet
Tür : SINDRI
Ürün Kodu : SINDRI 1243-01-01.FOSSIL
Müşteri Kodu : SINDRI 162
L2618500224 200002321 2605 20018663000301 40,4300 40,4300 12,0000 11,5000 0
Artikel 1 Top 40,4300 40,4300 12,0000 11,5000 0
Tür : ZIMMY
Ürün Kodu : ZIMMY.04
Müşteri Kodu : ZIMMY 162
L2619200001 200002348090626 20018210100101 50,3500 50,3500 17,3000 16,8000 0
L2619200001 200002348090626 20018210100102 44,1100 44,1100 15,2000 14,7000 0
Artikel 2 Top 94,4600 94,4600 32,5000 31,5000 0
Genel Toplam 3 Top 134,8900 134,8900 44,5000 43,0000 0
"""
    lines = lines_from_labeled_packing(text)
    articles = {line["article"] for line in lines}
    assert articles == {"SINDRI 162", "ZIMMY 162"}
    sindri = map_row(next(line["raw"] for line in lines if line["article"] == "SINDRI 162"))
    assert sindri["meters"] == 40.43
    assert sindri["rolls"] == 1
    assert sindri["net_weight"] == 11.5
    zimmy = map_row(next(line["raw"] for line in lines if line["article"] == "ZIMMY 162"))
    assert zimmy["rolls"] == 2
    assert round(zimmy["meters"], 2) == 94.46


def test_tosun_packing_pdf_and_bundle_do_not_crash() -> None:
    from pathlib import Path

    from app.models.enums import DocType
    from app.parsing.pipeline import parse_upload
    from app.services.profile_18233 import parse_bundle

    pdf = (
        Path(__file__).resolve().parents[2]
        / "documents"
        / "поставки"
        / "18259 тосун"
        / "исходники"
        / "упаковочный.pdf"
    )
    if not pdf.is_file():
        return
    docs = parse_upload(str(pdf), filename="упаковочный.pdf", allow_ocr=False)
    assert docs
    articles = {line.article for line in docs[0].lines}
    assert "SINDRI 162" in articles
    assert docs[0].doc_type == DocType.PACKING_LIST
    parse_bundle([pdf])


def test_goldluck_workbook_keeps_invoice_rows() -> None:
    from pathlib import Path

    from app.parsing.pipeline import parse_upload
    from app.services.reconcile import reconcile_documents

    path = Path(__file__).resolve().parents[2] / "documents" / "поставки" / "18018 пекин голдлак" / "исходники" / "поступление инвойс и пакинг.xlsx"
    if not path.is_file():
        return
    docs = parse_upload(str(path), filename=path.name, allow_ocr=False)
    items = reconcile_documents(docs)
    articles = [item.article for item in items]
    assert 30 <= len(items) <= 40
    assert "MD 812" in articles
    assert "NO228" in articles
    assert not any("A519-3" in (article or "") for article in articles)
    no228 = [item for item in items if item.article == "NO228"]
    assert sorted(item.commercial_data.get("qty") for item in no228) == [20, 1480]
    assert all(item.commercial_data.get("price") == 4.04 for item in no228)



