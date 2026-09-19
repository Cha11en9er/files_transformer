from __future__ import annotations

from pathlib import Path
from typing import Any

from app.parsing.anydoc_reader import read_anydoc
from app.parsing.languages import repair_mojibake
from app.parsing.normalize import normalize_text
from app.parsing.ocr import lines_from_ocr_blocks, ocr_image, ocr_pdf_pages
from app.parsing.table_rows import (
    lines_from_labeled_packing,
    lines_from_matrix,
    lines_from_plaintext,
    lines_from_qty_price_text,
    merge_line_groups,
)

MIN_SEARCHABLE_CHARS = 40


def _extract_with_pypdf(path: str) -> tuple[str, list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", ["pdf_reader_unavailable"]
    try:
        reader = PdfReader(path)
        parts = [(page.extract_text() or "") for page in reader.pages]
        return repair_mojibake("\n".join(parts)), []
    except Exception as exc:
        return "", [f"pypdf_failed:{exc}"]


def _extract_searchable_pdf(path: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    text_parts: list[str] = []
    lines: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        import pdfplumber
    except ImportError:
        text, extra = _extract_with_pypdf(path)
        warnings.extend(extra or ["pdfplumber_missing_used_pypdf"])
        if not text.strip():
            warnings.append("pdf_has_no_text_layer")
        return text, lines, warnings

    with pdfplumber.open(path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
            tables = page.extract_tables() or []
            for table_idx, table in enumerate(tables):
                if not table:
                    continue
                lines.extend(
                    lines_from_matrix(
                        table,
                        sheet_name=f"page_{page_idx + 1}_table_{table_idx + 1}",
                    )
                )
    text = repair_mojibake("\n".join(text_parts))
    packing = lines_from_labeled_packing(text, sheet_name="pdf_packing")
    if packing:
        lines = merge_line_groups(lines, packing)
    priced = lines_from_qty_price_text(text, sheet_name="pdf_qty_price")
    if not lines:
        lines = priced or lines_from_plaintext(text, sheet_name="pdf_text")
        if not lines:
            warnings.append("pdf_has_text_but_no_article_table")
    elif priced:
        lines = lines + priced
    return text, lines, warnings


def _should_ocr(*, text: str, lines: list[dict[str, Any]], needs_ocr: bool) -> bool:
    if needs_ocr:
        return True
    if lines:
        return False
    return len(normalize_text(text)) < max(MIN_SEARCHABLE_CHARS, 80)


def read_pdf(path: str, *, allow_ocr: bool = True) -> dict[str, Any]:
    anydoc_hit = read_anydoc(path) or {}
    needs_ocr = bool(anydoc_hit.get("needs_ocr"))
    ocr_pages = anydoc_hit.get("ocr_pages")
    try:
        text, lines, warnings = _extract_searchable_pdf(path)
    except Exception as exc:
        return {
            "text": anydoc_hit.get("text") or "",
            "lines": anydoc_hit.get("lines") or [],
            "warnings": [f"pdf_read_failed:{exc}"],
            "ocr_used": False,
            "ocr_confidence": None,
        }

    warnings.extend(anydoc_hit.get("warnings") or [])
    lines = merge_line_groups(lines, anydoc_hit.get("lines") or [])
    if anydoc_hit.get("text"):
        text = f"{anydoc_hit['text']}\n{text}".strip()

    ocr_used = False
    ocr_confidence: float | None = None

    if allow_ocr and _should_ocr(text=text, lines=lines, needs_ocr=needs_ocr):
        try:
            ocr_text, ocr_confidence, ocr_lines = ocr_pdf_pages(
                path,
                pages=ocr_pages if isinstance(ocr_pages, list) else None,
            )
            ocr_used = True
            text = (text + "\n" + ocr_text).strip()
            lines = merge_line_groups(lines, ocr_lines)
            if not lines:
                lines = merge_line_groups(
                    lines_from_qty_price_text(ocr_text, sheet_name="ocr_qty"),
                    lines_from_plaintext(ocr_text, sheet_name="ocr"),
                )
            if not lines:
                warnings.append("ocr_text_extracted_tables_not_detected")
        except Exception as exc:
            warnings.append(f"ocr_failed:{exc}")
    elif needs_ocr and not allow_ocr:
        warnings.append("pdf_not_searchable_ocr_disabled")

    return {
        "text": text,
        "lines": lines,
        "warnings": warnings,
        "ocr_used": ocr_used,
        "ocr_confidence": ocr_confidence,
    }


def read_image(path: str, *, allow_ocr: bool = True) -> dict[str, Any]:
    if not allow_ocr:
        return {
            "text": "",
            "lines": [],
            "warnings": ["image_ocr_disabled"],
            "ocr_used": False,
            "ocr_confidence": None,
        }
    try:
        text, conf, blocks = ocr_image(path)
        lines = lines_from_ocr_blocks(blocks, sheet_name="ocr_image")
        if not lines:
            lines = merge_line_groups(
                lines_from_qty_price_text(text, sheet_name="ocr_image_qty"),
                lines_from_plaintext(text, sheet_name="ocr_image_text"),
            )
        warnings = [] if lines else ["image_ocr_no_structured_table"]
        return {
            "text": text,
            "lines": lines,
            "warnings": warnings,
            "ocr_used": True,
            "ocr_confidence": conf,
        }
    except Exception as exc:
        return {
            "text": "",
            "lines": [],
            "warnings": [f"ocr_failed:{exc}"],
            "ocr_used": False,
            "ocr_confidence": None,
        }


def sniff_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return "excel"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        return "image"
    return "unknown"
