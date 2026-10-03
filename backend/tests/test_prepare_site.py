"""Новое чтение на сайте: столбцы таблицы и справочник вне лотов."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.prepare_site import (
    apply_verdict,
    filled_base_columns,
    is_reference_name,
    needs_second_model,
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
