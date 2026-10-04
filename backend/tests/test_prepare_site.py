"""Новое чтение на сайте: столбцы таблицы и справочник вне лотов."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.opencode_review import _model_parts
from app.services.prepare_site import (
    apply_verdict,
    filled_base_columns,
    header_from_draft,
    is_reference_name,
    lots_to_rows,
    needs_second_model,
    overlay_model_header,
    split_uploads,
)


def _lot(**fields):
    base = {"freight": False, "vendor": "", "description": "", "pieces": None, "packages": None}
    base.update(fields)
    return base


def test_partial_column_counts_as_filled() -> None:
    lots = [
        _lot(vendor="A", pieces=1, price=2, amount=2, hs="111"),
        _lot(vendor="B", pieces=3),
    ]
    # артикул, количество, цена, сумма, HS и тот же код в ТН ВЭД
    assert filled_base_columns(lots) == 6
    assert needs_second_model(lots) is False


def test_four_filled_columns_call_the_second_model() -> None:
    lots = [_lot(vendor="A", pieces=10, price=1, amount=10)]
    assert filled_base_columns(lots) == 4
    assert needs_second_model(lots) is True


def test_empty_cells_do_not_fill_a_column() -> None:
    lots = [_lot(vendor="A", description="   ", pieces=None, price="")]
    assert filled_base_columns(lots) == 1


def test_verdict_keeps_draft_numbers() -> None:
    draft = [_lot(vendor="A", pieces=10, price=1.5, amount=15)]
    payload = {"lots": [{"index": 0, "action": "keep", "fields": {}}]}
    out = apply_verdict(draft, payload)
    assert out[0]["pieces"] == 10
    assert out[0]["price"] == 1.5


def test_fix_does_not_wipe_a_number_with_empty() -> None:
    draft = [_lot(vendor="A", pieces=10, price=1.5)]
    payload = {"lots": [{"index": 0, "action": "fix", "fields": {"price": "", "pieces": 12}}]}
    out = apply_verdict(draft, {})
    assert out[0]["pieces"] == 10
    out = apply_verdict(draft, payload)
    assert out[0]["pieces"] == 12
    assert out[0]["price"] == 1.5


def test_grok_variant_is_not_part_of_the_model_id() -> None:
    provider, model_id, variant = _model_parts("openrouter/x-ai/grok-4.7:high")
    assert provider == "openrouter"
    assert model_id == "x-ai/grok-4.7"
    assert variant == "high"
    provider, model_id, variant = _model_parts("openrouter/x-ai/grok-4.7")
    assert model_id == "x-ai/grok-4.7"
    assert variant is None


def test_hs_is_digits_without_dots() -> None:
    rows = lots_to_rows(
        [_lot(vendor="BLOOM", hs="54.07.73.00.90.11", hs_alt="5407699000", pieces=49)],
        ["hs_conflict"],
    )
    customs = rows[0]["customs_data"]
    assert customs["hs_code"] == "540773009011"
    assert customs["tnved_code"] == "5407699000"
    assert "." not in customs["hs_code"]
    assert rows[0]["validation_errors"] == []


def test_draft_currency_reaches_the_header() -> None:
    # Валюта у колонки цены — USD. Она не должна теряться перед экспортом (иначе профиль ставит RMB).
    draft = {"currency": "USD", "seller": "ACME", "contract": "C-1"}
    header = header_from_draft(draft, [])
    assert header["currency"] == "USD"


def test_model_can_correct_the_currency() -> None:
    header = {"currency": "CNY"}
    payload = {"header": {"currency": "USD"}}
    out = overlay_model_header(header, payload)
    assert out["currency"] == "USD"


def test_empty_model_currency_does_not_wipe_the_draft() -> None:
    header = {"currency": "USD"}
    payload = {"header": {"currency": ""}}
    out = overlay_model_header(header, payload)
    assert out["currency"] == "USD"


def test_reference_file_stays_out_of_goods() -> None:
    saved = [
        ("c:/in/invoice.pdf", "invoice.pdf"),
        ("c:/in/сводная.xlsx", "сводная.xlsx"),
        ("c:/in/letter.pdf", "letter.pdf"),
    ]
    goods, references = split_uploads(saved, {"letter.pdf"})
    assert [name for _path, name in goods] == ["invoice.pdf"]
    assert {name for _path, name in references} == {"сводная.xlsx", "letter.pdf"}
    assert is_reference_name("справочник.xlsx", set())
