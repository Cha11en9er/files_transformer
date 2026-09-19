from __future__ import annotations

from pathlib import Path

from app.models.enums import DocType
from app.parsing.anydoc_reader import read_anydoc
from app.parsing.classifier import classify_document
from app.parsing.excel_reader import read_excel, read_excel_sheets
from app.parsing.pdf_extractor import read_image, read_pdf, sniff_kind
from app.parsing.schemas import ParsedDocument, ParsedLine
from app.parsing.table_rows import merge_line_groups


def _as_lines(raw_lines: list[dict]) -> list[ParsedLine]:
    return [ParsedLine.model_validate(item) for item in raw_lines]


def _apply_classification(document: ParsedDocument, *, filename: str) -> ParsedDocument:
    classification = classify_document(
        filename=filename,
        text=document.text_preview,
        sheet_names=document.sheets,
        use_filename=False,
    )
    document.doc_type = classification.doc_type
    document.classification_score = classification.score
    document.classification_hits = classification.hits
    if document.doc_type is None and document.lines:
        document.doc_type = DocType.INVOICE
        document.warnings.append("doc_type_defaulted_invoice")
    elif classification.doc_type is None:
        document.warnings.append("doc_type_unclassified_needs_operator")
    return document


def parse_file(path: str, *, filename: str | None = None, allow_ocr: bool = True) -> ParsedDocument:
    file_path = str(path)
    name = filename or Path(path).name
    kind = sniff_kind(file_path)

    document = ParsedDocument(filename=name, file_path=file_path, mime_hint=kind)
    anydoc_hit = read_anydoc(file_path) if kind == "excel" else None
    anydoc_lines = (anydoc_hit or {}).get("lines") or []

    try:
        if kind == "excel":
            sheets, pandas_lines, preview = read_excel(file_path)
            document.sheets = sheets
            document.text_preview = preview
            if anydoc_hit and anydoc_hit.get("text"):
                document.text_preview = f"{anydoc_hit['text']}\n{preview}"
            document.lines = _as_lines(merge_line_groups(pandas_lines, anydoc_lines))
        elif kind == "pdf":
            extracted = read_pdf(file_path, allow_ocr=allow_ocr)
            document.text_preview = extracted["text"]
            document.ocr_used = extracted["ocr_used"]
            document.ocr_confidence = extracted["ocr_confidence"]
            document.warnings.extend(extracted["warnings"])
            document.lines = _as_lines(extracted["lines"])
        elif kind == "image":
            extracted = read_image(file_path, allow_ocr=allow_ocr)
            document.text_preview = extracted["text"]
            document.ocr_used = extracted["ocr_used"]
            document.ocr_confidence = extracted["ocr_confidence"]
            document.warnings.extend(extracted["warnings"])
            document.lines = _as_lines(extracted["lines"])
        else:
            document.warnings.append(f"unsupported_extension:{Path(file_path).suffix}")
    except Exception as exc:
        document.warnings.append(f"parse_failed:{exc}")
        if anydoc_lines:
            document.text_preview = (anydoc_hit or {}).get("text") or ""
            document.lines = _as_lines(anydoc_lines)

    return _apply_classification(document, filename=name)


def parse_upload(path: str, *, filename: str | None = None, allow_ocr: bool = True) -> list[ParsedDocument]:
    """Parse a file; split Excel into one document per sheet when types differ."""
    file_path = str(path)
    name = filename or Path(path).name
    if sniff_kind(file_path) != "excel":
        return [parse_file(file_path, filename=name, allow_ocr=allow_ocr)]

    try:
        sheets = read_excel_sheets(file_path)
    except Exception:
        return [parse_file(file_path, filename=name, allow_ocr=allow_ocr)]

    built: list[ParsedDocument] = []
    for sheet in sheets:
        if not sheet.lines and len(sheet.preview.strip()) < 12:
            continue
        classification = classify_document(
            filename="",
            text=sheet.preview,
            sheet_names=[sheet.name],
            use_filename=False,
        )
        document = ParsedDocument(
            filename=f"{name} · {sheet.name}",
            file_path=file_path,
            mime_hint="excel",
            sheets=[sheet.name],
            text_preview=sheet.preview,
            lines=_as_lines(sheet.lines),
            doc_type=classification.doc_type,
            classification_score=classification.score,
            classification_hits=classification.hits,
        )
        if classification.doc_type is None:
            document.warnings.append("doc_type_unclassified_needs_operator")
        built.append(document)

    typed = {doc.doc_type for doc in built if doc.doc_type is not None}
    if len(typed) <= 1:
        return [parse_file(file_path, filename=name, allow_ocr=allow_ocr)]

    result: list[ParsedDocument] = []
    for document in built:
        if document.doc_type is None and not document.lines:
            continue
        if document.doc_type is None and document.lines:
            document.doc_type = DocType.INVOICE
            document.warnings.append("doc_type_defaulted_invoice")
        result.append(document)
    return result or [parse_file(file_path, filename=name, allow_ocr=allow_ocr)]
