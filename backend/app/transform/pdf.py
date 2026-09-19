"""Safe PDF reading.

Prefer pdfplumber (tables + text). If it is missing, fall back to pypdf text
and split lines into a grid so the rest of transform/ still sees a sheet.
We deliberately do NOT run heavy OCR inside the web process — scanned PDFs
without a text layer are reported as such; the vision model reads page images.
"""

from __future__ import annotations

from pathlib import Path

from app.transform.reader import Sheet

MAX_PAGES = 25


class PdfReadResult:
    def __init__(self, sheets: list[Sheet], text: str, scanned: bool) -> None:
        self.sheets = sheets
        self.text = text
        self.scanned = scanned


def read_pdf(path: str) -> PdfReadResult:
    plumber = _read_pdfplumber(path)
    if plumber is not None:
        return plumber
    return _read_pypdf(path)


def _read_pdfplumber(path: str) -> PdfReadResult | None:
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001
        return None

    name = Path(path).name
    sheets: list[Sheet] = []
    texts: list[str] = []
    table_settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages[:MAX_PAGES]):
                page_text = (page.extract_text() or "").strip()
                if page_text:
                    texts.append(page_text)
                tables = []
                try:
                    tables = page.extract_tables(table_settings) or []
                    if not tables:
                        tables = page.extract_tables() or []
                except Exception:  # noqa: BLE001
                    tables = []
                for t_index, table in enumerate(tables):
                    grid = [[(c if c not in ("", None) else None) for c in row] for row in table]
                    if len(grid) >= 2:
                        sheets.append(Sheet(name=f"p{i + 1}t{t_index + 1}", grid=grid, source=name))
                if not tables and page_text:
                    grid = _grid_from_text(page_text)
                    if len(grid) >= 3:
                        sheets.append(Sheet(name=f"p{i + 1}text", grid=grid, source=name))
    except Exception:  # noqa: BLE001
        if not texts and not sheets:
            return None
        full_text = "\n".join(texts)
        return PdfReadResult(sheets, full_text, scanned=not sheets and not texts)

    full_text = "\n".join(texts)
    scanned = not full_text and not sheets
    return PdfReadResult(sheets, full_text, scanned=scanned)


def _read_pypdf(path: str) -> PdfReadResult:
    name = Path(path).name
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001
        return PdfReadResult([], "", scanned=True)
    try:
        from app.parsing.languages import repair_mojibake
    except Exception:  # noqa: BLE001
        def repair_mojibake(text: str) -> str:  # type: ignore[misc]
            return text

    texts: list[str] = []
    sheets: list[Sheet] = []
    try:
        reader = PdfReader(path)
        for i, page in enumerate(reader.pages[:MAX_PAGES]):
            page_text = repair_mojibake((page.extract_text() or "")).strip()
            if not page_text:
                continue
            texts.append(page_text)
            grid = _grid_from_text(page_text)
            if len(grid) >= 3:
                sheets.append(Sheet(name=f"p{i + 1}text", grid=grid, source=name))
    except Exception:  # noqa: BLE001
        full_text = "\n".join(texts)
        return PdfReadResult(sheets, full_text, scanned=not sheets and not texts)

    full_text = "\n".join(texts)
    scanned = not full_text and not sheets
    return PdfReadResult(sheets, full_text, scanned=scanned)


def _grid_from_text(text: str) -> list[list[str]]:
    """Split each line on runs of 2+ spaces (borderless invoices/packing)."""
    import re

    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        cells = re.split(r"\s{2,}|\t", line.strip())
        rows.append([c.strip() for c in cells])
    return rows
