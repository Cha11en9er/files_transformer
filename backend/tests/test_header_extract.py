from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.parsing.header_extract import extract_header_fields, merge_header_fields
from app.parsing.schemas import ParsedDocument


def test_header_from_zhongfang_invoice_and_packing() -> None:
    invoice = ParsedDocument(
        filename="инвойс.xlsx",
        file_path="инвойс.xlsx",
        mime_hint="excel",
        text_preview=(
            "HANGZHOU ZHONGFANG TEXTILE IMP./EXP. CO., LTD.\n"
            "COMMERCIAL INVOICE\n"
            "Buyer:“SM REGIONTEKSTIL'” LLC / 143421, Moscow region\n"
            "EX-WORK HANGZHOU | INV.NO. | ZFRMB26148-626-1\n"
            "Contract No SM-LU2 dd 23/11/2018 | DATE: | Aug.19,2026\n"
        ),
    )
    packing = ParsedDocument(
        filename="упаковочный.xlsx",
        file_path="упаковочный.xlsx",
        mime_hint="excel",
        text_preview=(
            "HANGZHOU ZHONGFANG TEXTILE IMP./EXP. CO., LTD.\n"
            "PACKING LIST\n"
            "INV.NO. | ZFRMB26148-626-1\n"
        ),
    )
    catalog = ParsedDocument(
        filename="справочник сводная.xlsx",
        file_path="x.xlsx",
        mime_hint="excel",
        text_preview="INVOICE NO.: | HZZF19017\nBuyer:“ELEMENT” LLC | DATE: | 2019-01-22",
    )
    header = extract_header_fields([invoice, packing, catalog])
    assert header["invoice_no"] == "ZFRMB26148-626-1"
    assert header["contract_no"] == "SM-LU2"
    assert "23/11/2018" in header["contract_date"]
    assert "Aug.19" in header["invoice_date"]
    assert "REGIONTEKSTIL" in header["buyer"].upper()
    assert "143421" in (header.get("buyer_address") or "")
    assert "ZHONGFANG" in header["seller"].upper()
    assert "EX-WORK" in header["delivery_terms"].upper()
    assert header["invoice_no"] != "HZZF19017"


def test_header_from_beijing_sheets_and_pdf() -> None:
    excel = ParsedDocument(
        filename="поступление инвойс и пакинг.xlsx",
        file_path="x.xlsx",
        mime_hint="excel",
        text_preview=(
            "Invoice and Packing list\n"
            'TO: "ELEMENT" LLC | Contract No.: | DJO-3\n'
            "Container No.: | GLLU9211190 | Invoice No.: | 26BEET-003\n"
            "BEIJING GOLDLUCK CO., LTD\n"
        ),
    )
    pdf = ParsedDocument(
        filename="подписанный скан.pdf",
        file_path="x.pdf",
        mime_hint="pdf",
        text_preview="Commercial invoice Invoice No: 26BEET-003 Buyer: ELEMENT LLC",
    )
    header = extract_header_fields([excel, pdf])
    assert header["invoice_no"] == "26BEET-003"
    assert header["contract_no"] == "DJO-3"
    assert "ELEMENT" in header["buyer"].upper()
    assert "GLLU9211190" in header["container_no"]


def test_header_conflict_is_not_picked() -> None:
    left = ParsedDocument(
        filename="a.xlsx",
        file_path="a.xlsx",
        mime_hint="excel",
        text_preview="INV.NO. AAA-1",
    )
    right = ParsedDocument(
        filename="b.xlsx",
        file_path="b.xlsx",
        mime_hint="excel",
        text_preview="INV.NO. BBB-2",
    )
    header = extract_header_fields([left, right])
    assert "invoice_no" not in header


def test_merge_fills_gaps_and_keeps_excel() -> None:
    base = {"invoice_no": "ZFRMB26148-626-1", "buyer": "SM"}
    incoming = {"invoice_no": "OTHER", "seller": "HANGZHOU ZHONGFANG TEXTILE IMP./EXP. CO., LTD."}
    merged = merge_header_fields(base, incoming)
    assert merged["invoice_no"] == "ZFRMB26148-626-1"
    assert "ZHONGFANG" in merged["seller"]


def test_letterhead_grid_splits_buyer_and_date() -> None:
    from app.parsing.header_extract import extract_header_from_letterheads

    buyer_cell = (
        "Buyer:“SM REGIONTEKSTIL'” LLC\n"
        "143421, Moscow region, Krasnogorsk city, ter. Baltiya road, km 21, h. 2, str. 1, room 1\n"
        "OGRN 1175024014472    TIN 5032281280    KPP 502401001"
    )
    rows = [
        ["HANGZHOU ZHONGFANG TEXTILE IMP./EXP. CO., LTD."],
        ["COMMERCIAL INVOICE"],
        [buyer_cell],
        ["EX-WORK HANGZHOU", "", "", "", "", "", "", "", "INV.NO.", "ZFRMB26148-626-1"],
        ["Contract No SM-LU2 dd 23/11/2018", "", "", "", "", "", "", "", "DATE:", "Aug.19,2026"],
    ]
    header = extract_header_from_letterheads([("invoice.xlsx", rows)])
    assert "REGIONTEKSTIL" in header["buyer"].upper()
    assert "LLC" in header["buyer"].upper()
    assert "143421" in header["buyer_address"]
    assert "OGRN" in header["buyer_address"].upper()
    assert header["invoice_no"] == "ZFRMB26148-626-1"
    assert "Aug.19" in header["invoice_date"]
    assert header["contract_no"] == "SM-LU2"


def test_header_from_invoice_colon_and_company_limited() -> None:
    invoice = ParsedDocument(
        filename="INV_WAY05_1.pdf",
        file_path="INV_WAY05_1.pdf",
        mime_hint="pdf",
        text_preview=(
            "HMK TRADING COMPANY LIMITED\n"
            "INVOICE: NH-331005\n"
            "DATE: 15.03.2026\n"
            "THE SELLER:\n"
            "HMK TRADING COMPANY LIMITED\n"
            "Address: RM1607 TREND CTR 29-31 CHEUNG LEE ST CHAIWAN HONGKONG\n"
            "CONTRACT: NEC-01/10 dd 01.10.2025\n"
            "THE BUYER:\n"
            "LLC NECARGO\n"
            "Address: 142116, Moscow region, g.o. Podolsk\n"
            "Terms of delivery: FCA Shanghai (Incoterms 2020)\n"
            "CONTAINER: SORU4033371\n"
        ),
    )
    header = extract_header_fields([invoice])
    assert header["invoice_no"] == "NH-331005"
    assert "HMK" in header["seller"].upper()
    assert header.get("manufacturer") in (None, "")
    assert "NECARGO" in header["buyer"].upper()
    assert header["contract_no"].replace("С", "C").startswith("NEC-01")
    assert "15.03.2026" in header["invoice_date"]
    assert "01.10.2025" in (header.get("contract_date") or "")
    assert "SHANGHAI" in header["delivery_terms"].upper()
    assert "INCOTERM" in header["delivery_terms"].upper()
    assert "TREND" in (header.get("seller_address") or "").upper() or "HONGKONG" in (header.get("seller_address") or "").upper()
    assert "PODOLSK" in (header.get("buyer_address") or "").upper() or "142116" in (header.get("buyer_address") or "")
    assert "SORU4033371" in header["container_no"]


def test_enrich_header_keeps_seller_and_maker_apart() -> None:
    from app.parsing.header_extract import enrich_header_from_goods, currency_from_sources, export_header_fields

    header = {
        "seller": "HMK TRADING COMPANY LIMITED",
        "manufacturer": "HMK TRADING COMPANY LIMITED",
        "delivery_terms": (
            "Payments are made by simple bank transfer in RUR, and can be made "
            "in Chinese yuan, US dollars."
        ),
    }
    items = [
        {
            "commercial_data": {"currency": "USD"},
            "customs_data": {"manufacturer": "Bestway (Nantong) Recreation Corp. / Bestway"},
        }
        for _ in range(5)
    ]
    enriched = enrich_header_from_goods(header, items)
    assert "BESTWAY" in enriched["manufacturer"].upper()
    assert "HMK" not in enriched["manufacturer"].upper()
    assert enriched["currency"] == "USD"
    assert currency_from_sources([], header) is None

    # Operator edit must survive export packaging.
    edited = export_header_fields(
        {**enriched, "manufacturer": "Intex Industries (Fujian) Co., Ltd / INTEX", "payment_terms": "Net 90"},
        items,
    )
    assert "INTEX" in edited["manufacturer"].upper()
    assert edited["payment_terms"] == "Net 90"


def test_payment_prose_does_not_steal_contract_no() -> None:
    invoice = ParsedDocument(
        filename="INV.pdf",
        file_path="INV.pdf",
        mime_hint="pdf",
        text_preview=(
            "CONTRACT: NEC-01/10 dd 01.10.2025\n"
            "The Buyer pays for the Goods within 90 days after customs clearance.\n"
            "Conversion into the currency of the contract in this case is made at the exchange rate.\n"
        ),
    )
    header = extract_header_fields([invoice])
    assert header["contract_no"].replace("С", "C").startswith("NEC-01")
    assert "01.10.2025" in (header.get("contract_date") or "")


def test_dap_incoterm_and_street_continuation() -> None:
    invoice = ParsedDocument(
        filename="INV.pdf",
        file_path="INV.pdf",
        mime_hint="pdf",
        text_preview=(
            "INVOICE: IDV3040C\n"
            "THE SELLER:\n"
            "HMK TRADING COMPANY LIMITED\n"
            "Address: RM1607 TREND CTR 29-31 CHEUNG LEE ST CHAIWAN HONGKONG\n"
            "THE BUYER:\n"
            "LLC NECARGO\n"
            "Address: 117534, Russia, Moscow, internal territorial city municipal district Yuzhnoye Chertanovo,\n"
            "Chertanovskaya St., 66, bldg. 2, apt. 142\n"
            "Terms of delivery: DAP Moscow (Incoterms 2020)\n"
        ),
    )
    header = extract_header_fields([invoice])
    assert header["invoice_no"] == "IDV3040C"
    assert "DAP" in header["delivery_terms"].upper()
    assert "MOSCOW" in header["delivery_terms"].upper()
    assert "117534" in (header.get("buyer_address") or "")
    assert "CHERTANOVSKAYA" in (header.get("buyer_address") or "").upper()
    assert "HONGKONG" in (header.get("seller_address") or "").upper()
