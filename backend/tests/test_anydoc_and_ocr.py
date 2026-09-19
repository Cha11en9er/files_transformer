from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.parsing.anydoc_reader import _lines_from_markdown
from app.parsing.ocr import rows_from_ocr_blocks
from app.parsing.table_rows import lines_from_qty_price_text


TOSUN_MD = """
||||I N V O I C E|||||
|D E S C R I P T I O N||QUANTITY MT.|EXW USD||TRY|TOTAL EXW USD|TOTAL EXW TRY|
||ZIMMY|1.740,82|MT. 5,78 USD||287,97 TL|10.061,93 USD|501.307,77 TL|
||SINDRI|40,43|MT. 6,17 USD||307,40 TL|249,45 USD|12.428,30 TL|
"""

WEAVERS_MD = """
|Desing No / Design Name / Weavers Code / Item No / PO|Quantity|Unit Price|Total|
|---|---|---|---|
||206,00 212,00|MT MT|793,10 $ 816,20 $|

D15-5745 / DYER 789 / PX83.MAVI 999 / 0 / new order 04.6 / HS CODE : 540753009011
Y18-8687 / LARDASO 100 / PX92.BEYAZ 256 / 0 / new order 3.7 / HS CODE : 540753009011
"""


def test_anydoc_markdown_reads_tosun_invoice_rows() -> None:
    lines = _lines_from_markdown(TOSUN_MD)
    articles = {str(line.get("article") or "").upper() for line in lines}
    assert any("ZIMMY" in a for a in articles)
    assert any("SINDRI" in a for a in articles)


def test_qty_price_reads_european_meters() -> None:
    text = "ZIMMY 1.740,82 MT. 5,78 USD 10.061,93 USD"
    lines = lines_from_qty_price_text(text)
    assert lines
    assert "ZIMMY" in str(lines[0]["article"]).upper()


def test_anydoc_markdown_reads_weavers_designs() -> None:
    lines = _lines_from_markdown(WEAVERS_MD)
    articles = {str(line.get("article") or "").upper() for line in lines}
    assert any("DYER" in a for a in articles)
    assert any("LARDASO" in a for a in articles)


def test_ipekis_garbled_usd_and_mill_article() -> None:
    text = (
        "7508 11101 0801 110501 0/o 100 PES FABRIC ROMO 42,00 MT 9,95 USO 417,90 USO\n"
        "0/o 100 PES FABRIC ROMO 42,00 MT 9,95 USO 417,90 USO 7508 11 101 0801 110501\n"
    )
    lines = lines_from_qty_price_text(text)
    articles = {str(line.get("article") or "").upper() for line in lines}
    assert any(a.replace(" ", "").isdigit() and "7508" in a for a in articles)
    assert "ROMO" not in articles


def test_ocr_blocks_group_into_rows() -> None:
    blocks = [
        {"text": "Article", "box": [[10, 10], [80, 10], [80, 20], [10, 20]]},
        {"text": "Qty", "box": [[120, 10], [160, 10], [160, 20], [120, 20]]},
        {"text": "ZIMMY 162", "box": [[10, 40], [90, 40], [90, 52], [10, 52]]},
        {"text": "12", "box": [[120, 40], [150, 40], [150, 52], [120, 52]]},
    ]
    rows = rows_from_ocr_blocks(blocks)
    assert rows[0] == ["Article", "Qty"]
    assert rows[1][0] == "ZIMMY 162"
    assert "12" in rows[1]
