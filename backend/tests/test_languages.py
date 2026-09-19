from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.parsing.languages import (
    LANGUAGES,
    decode_bytes,
    detect_languages,
    detect_scripts,
    language_prompt_block,
    repair_mojibake,
)
from app.services.field_map import classify_header


def test_languages_registry_is_extensible() -> None:
    ids = {spec.id for spec in LANGUAGES}
    assert {"ru", "en", "zh", "tr", "it"} <= ids


def test_detect_scripts_and_languages_on_mixed_shipment() -> None:
    blob = (
        "杭州中纺进出口有限公司 COMMERCIAL INVOICE Noble 110 "
        "артикул количество Müşteri Kodu SINDRI 162 Foglio1 HIDES"
    )
    scripts = detect_scripts(blob)
    langs = detect_languages(blob)
    assert "cjk" in scripts
    assert "cyrillic" in scripts
    assert "zh" in langs
    assert "ru" in langs
    assert "tr" in langs
    assert "en" in langs
    assert "it" in langs
    notes = language_prompt_block(blob)
    assert "languages_in_this_shipment" in notes
    assert "Chinese" in notes
    assert "new language" in notes


def test_decode_cp1251_and_gb18030() -> None:
    russian, ru_enc = decode_bytes("Артикул количество".encode("cp1251"))
    assert russian == "Артикул количество"
    assert ru_enc == "cp1251"
    chinese, zh_enc = decode_bytes("杭州中纺进出口有限公司".encode("gb18030"))
    assert chinese == "杭州中纺进出口有限公司"
    assert zh_enc in {"gb18030", "gbk"}
    turkish, tr_enc = decode_bytes("Müşteri Kodu".encode("cp1254"))
    assert "Müşteri" in turkish
    assert tr_enc in {"cp1254", "iso-8859-9"}


def test_decode_utf8_preferred_for_mixed_unicode() -> None:
    text = "Noble 110 / артикул / 货号"
    decoded, encoding = decode_bytes(text.encode("utf-8"))
    assert decoded == text
    assert encoding.startswith("utf-8")


def test_repair_utf8_shown_as_latin1() -> None:
    original = "Количество"
    mojibake = original.encode("utf-8").decode("latin-1")
    assert "Колич" not in mojibake
    assert repair_mojibake(mojibake) == original


def test_header_synonyms_pattern_hides_ceki_fields() -> None:
    assert classify_header("Pattern") == "article"
    assert classify_header("HIDES") == "qty"
    assert classify_header("Total Roll") == "rolls"
    assert classify_header("Total Meter") == "meters"
    assert classify_header("Euro/m2") == "price"
    assert classify_header("MAL CINSI") == "description"
    assert classify_header("Art No.") == "article"
    assert classify_header("净重") == "net_weight"


def test_total_m2_is_area_not_amount() -> None:
    from app.services.field_map import is_factory_note, map_row

    assert classify_header("TOTAL M2") == "area"
    assert classify_header("Total M²") == "area"
    assert classify_header("AMOUNT(RMB)") == "amount"
    assert classify_header("UNIT PRICE(RMB)") == "price"
    mapped = map_row(
        {
            "DESIGN": "Sherlock 980",
            "TOTAL M2": 2736.15,
            "METERS": 1887,
            "UNIT PRICE(RMB)": 10.4,
            "AMOUNT(RMB)": 19624.8,
        }
    )
    assert mapped["area"] == 2736.15
    assert mapped["meters"] == 1887
    assert mapped["price"] == 10.4
    assert mapped["amount"] == 19624.8
    confused = map_row(
        {
            "DESIGN": "Sherlock 980",
            "TOTAL M2": 2736.15,
            "METERS": 1887,
            "UNIT PRICE(RMB)": 10.4,
        }
    )
    assert abs(float(confused["amount"]) - 19624.8) < 0.05
    assert is_factory_note("(15+30)")
    assert is_factory_note("(A)")
    assert is_factory_note("0605 Special Order")
    assert not is_factory_note("Upholstery fabric from polyether fiber")
