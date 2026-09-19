from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.routes import shipments as shipments_route
from app.models.enums import DocType
from app.parsing.pdf_extractor import sniff_kind


def test_sniff_kind_recognizes_pdf() -> None:
    assert sniff_kind("invoice.pdf") == "pdf"
    assert sniff_kind("scan.PNG") == "image"
    assert sniff_kind("book.xlsx") == "excel"


def test_allow_ocr_for_pdf_and_images() -> None:
    assert shipments_route._allow_ocr_for_upload(Path("a.pdf")) is True
    assert shipments_route._allow_ocr_for_upload(Path("a.jpg")) is True
    assert shipments_route._allow_ocr_for_upload(Path("a.xlsx")) is False


def test_validate_upload_rejects_unknown_extension() -> None:
    with pytest.raises(HTTPException) as exc:
        shipments_route._validate_upload_filename("notes.doc")
    assert exc.value.status_code == 400
    assert "не поддерживается" in exc.value.detail


def test_guess_doc_type_keeps_content_over_filename() -> None:
    assert (
        shipments_route._guess_doc_type("INVOICE.xlsx", DocType.PACKING_LIST)
        == DocType.PACKING_LIST
    )


def test_guess_doc_type_filename_fallback() -> None:
    assert shipments_route._guess_doc_type("18233 626-1 описание.pdf", None) == DocType.SPECIFICATION
    assert shipments_route._guess_doc_type("permit_rd.pdf", None) == DocType.PERMIT


def test_mixed_shipment_rejects_two_kits() -> None:
    msg = shipments_route.mixed_shipment_error(
        [
            "18233 ханчжоу 626-1/исходники/инвойс.xlsx",
            "18233 ханчжоу 626-2/исходники/инвойс.xlsx",
        ]
    )
    assert msg is not None
    assert "626-1" in msg and "626-2" in msg


def test_mixed_shipment_rejects_two_jobs() -> None:
    msg = shipments_route.mixed_shipment_error(
        [
            "18018 пекин голдлак/исходники/поступление инвойс и пакинг.xlsx",
            "18049 мора/исходники/инвойс.pdf",
        ]
    )
    assert msg is not None
    assert "18018" in msg and "18049" in msg


def test_single_shipment_folder_is_ok() -> None:
    assert (
        shipments_route.mixed_shipment_error(
            [
                "18233 ханчжоу 626-1/исходники/инвойс.xlsx",
                "18233 ханчжоу 626-1/исходники/упаковочный.xlsx",
            ]
        )
        is None
    )


def test_same_job_number_in_several_filenames_is_one_kit() -> None:
    assert (
        shipments_route.mixed_shipment_error(
            [
                "MORA/18049 Инвойс.pdf",
                "MORA/PACKING LIST 2026 - 69.xls",
                "MORA/СПЕЦИФИКАЦИЯ 18049.xlsx",
                "MORA/customsagency@wp.pl_20260713_084506_8778.pdf",
            ]
        )
        is None
    )
    assert (
        shipments_route.mixed_shipment_error(
            [
                "18049 Инвойс.pdf",
                "СПЕЦИФИКАЦИЯ 18049.xlsx",
            ]
        )
        is None
    )


def test_filter_upload_skips_junk_and_ds_folder() -> None:
    assert shipments_route._is_skippable_upload("notes.doc") is True
    assert shipments_route._is_skippable_upload("kit/ДС/китай.pdf") is True
    assert shipments_route._is_skippable_upload("invoice.xlsx") is False


def test_guess_doc_type_packing_filename_overrides_default_invoice() -> None:
    assert shipments_route._guess_doc_type("упаковочный.pdf", DocType.INVOICE) == DocType.PACKING_LIST


def test_humanize_excel_engine_error() -> None:
    from app.parsing.user_messages import humanize_message

    ru = humanize_message(
        "parse_failed:Excel file format cannot be determined, you must specify an engine manually."
    )
    assert "Excel" in ru
    assert "engine" not in ru.lower()
    assert "must specify" not in ru.lower()
    table = humanize_message("pdf_has_text_but_no_article_table")
    assert "PDF" in table
    assert "pdf_has_text" not in table


def test_humanize_opencode_connection_refused() -> None:
    from app.parsing.user_messages import humanize_message

    ru = humanize_message(
        "[WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение"
    )
    assert "OpenCode" in ru
    assert "10061" not in ru


def test_humanize_opencode_serveerror() -> None:
    from app.parsing.user_messages import humanize_message

    ru = humanize_message("Error: Unexpected error\nServeError")
    assert "4096" in ru
    assert "ServeError" not in ru
    assert "Unexpected" not in ru


def test_humanize_zen_credits() -> None:
    from app.parsing.user_messages import humanize_message

    ru = humanize_message(
        'Provider request failed with HTTP 401: {"type":"CreditsError","message":"Insufficient balance"}'
    )
    assert "баланс" in ru.lower() or "оплат" in ru.lower()
    assert "пароль" not in ru.lower()


def test_dedupe_uploads_same_bytes_different_names() -> None:
    from io import BytesIO

    from starlette.datastructures import UploadFile

    payload = b"%PDF-1 same-bytes-here"
    first = UploadFile(filename="инвойс.pdf", file=BytesIO(payload))
    copy = UploadFile(filename="copy.pdf", file=BytesIO(payload))
    other = UploadFile(filename="other.pdf", file=BytesIO(payload + b"-other"))
    kept = shipments_route._dedupe_uploads([first, copy, other])
    assert [item.filename for item in kept] == ["инвойс.pdf", "other.pdf"]


def test_critical_parse_skips_file_not_soft_ocr() -> None:
    assert shipments_route.classify_parse_result(["parse_failed:broken"])[0] == "skipped"
    assert shipments_route.classify_parse_result(["pdf_has_text_but_no_article_table"])[0] == "review"
    assert shipments_route.classify_parse_result(["ocr_failed:timeout"])[0] == "review"
    assert shipments_route.classify_parse_result([], had_exception=True)[0] == "skipped"
    assert shipments_route.classify_parse_result([])[0] == "ok"


def test_transform_pdf_falls_back_to_pypdf(tmp_path: Path, monkeypatch) -> None:
    from pypdf import PdfWriter

    from app.transform import pdf as pdf_mod

    path = tmp_path / "packing.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "test"})
    with path.open("wb") as fh:
        writer.write(fh)

    monkeypatch.setattr(pdf_mod, "_read_pdfplumber", lambda _path: None)
    result = pdf_mod.read_pdf(str(path))
    assert result.scanned in {True, False}
    assert isinstance(result.text, str)
    assert isinstance(result.sheets, list)

