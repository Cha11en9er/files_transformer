"""Safe PDF reading.

Prefer pdfplumber (tables + text). If it is missing, fall back to pypdf text
and split lines into a grid so the rest of transform/ still sees a sheet.
We deliberately do NOT run heavy OCR inside the web process — scanned PDFs
without a text layer are reported as such; the vision model reads page images.

Tables often span pages: page 2+ may continue the same columns without a header,
or with a stacked two-row header (WEIGHT + NETTO / BRUTTO). Those pages are
stitched, not treated as a new document.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.transform.canonical import is_number_like
from app.transform.reader import Sheet

MAX_PAGES = 25

_HEADER_HINT = re.compile(
    r"description|qty|quantity|article|art\.|weight|amount|origin|"
    r"manufacturer|package|model|series|netto|brutto|price|code|"
    r"наименован|артикул|количество|нетто|брутто|цена|сумма|страна",
    re.IGNORECASE,
)
_TABLE_SETTINGS = (
    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    {"vertical_strategy": "lines", "horizontal_strategy": "text"},
    {"vertical_strategy": "text", "horizontal_strategy": "text"},
)


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


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def row_header_score(row: list[object]) -> int:
    texts = [_cell_text(c) for c in row]
    joined = " ".join(texts)
    if not joined:
        return 0
    hints = len(_HEADER_HINT.findall(joined))
    nums = sum(1 for text in texts if text and is_number_like(text) and not _HEADER_HINT.search(text))
    if nums >= 2:
        return 0
    return hints


def flatten_header_rows(grid: list[list[object]]) -> list[list[object]]:
    """Merge stacked header rows (WEIGHT + NETTO / BRUTTO) into one title row."""
    if not grid:
        return grid
    cleaned = [list(row) for row in grid]
    while cleaned and not any(_cell_text(c) for c in cleaned[0]):
        cleaned = cleaned[1:]
    if not cleaned:
        return cleaned
    header_count = 0
    for row in cleaned[:4]:
        if row_header_score(row) >= 2:
            header_count += 1
            continue
        break
    if header_count < 2:
        return cleaned
    width = max(len(row) for row in cleaned[:header_count])
    merged: list[object] = []
    for col in range(width):
        parts: list[str] = []
        for row in cleaned[:header_count]:
            text = _cell_text(row[col] if col < len(row) else None)
            if text and text not in parts:
                parts.append(text)
        merged.append(" ".join(parts) if parts else None)
    return [merged, *cleaned[header_count:]]


def _table_rank(table: list[list[object]]) -> tuple[int, int]:
    rows = [row for row in table if any(_cell_text(c) for c in row)]
    width = max((len(row) for row in rows), default=0)
    return (len(rows), width)


def _best_tables(page) -> list[list[list[object]]]:
    found: list[list[list[object]]] = []
    for settings in _TABLE_SETTINGS:
        try:
            tables = page.extract_tables(settings) or []
        except Exception:  # noqa: BLE001
            tables = []
        for table in tables:
            if table and _table_rank(table)[0] >= 2:
                found.append(table)
        if found:
            break
    if not found:
        try:
            tables = page.extract_tables() or []
        except Exception:  # noqa: BLE001
            tables = []
        found = [table for table in tables if table]
    found.sort(key=_table_rank, reverse=True)
    return found


def _as_grid(table: list[list[object]]) -> list[list[object]]:
    grid = [[(c if c not in ("", None) else None) for c in row] for row in table]
    return flatten_header_rows(grid)


def stitch_continuation_tables(pages: list[tuple[int, list[list[object]]]]) -> list[tuple[int, list[list[object]]]]:
    """Carry the first page header onto later pages that only have data rows."""
    stitched: list[tuple[int, list[list[object]]]] = []
    header: list[object] | None = None
    header_width = 0
    for page_no, grid in pages:
        if not grid:
            continue
        body = flatten_header_rows(grid)
        if not body:
            continue
        looks_header = row_header_score(body[0]) >= 2
        if looks_header:
            header = list(body[0])
            header_width = len(header)
            stitched.append((page_no, body))
            continue
        if header and abs(len(body[0]) - header_width) <= 2:
            stitched.append((page_no, [list(header), *body]))
            continue
        stitched.append((page_no, body))
    return stitched


def _read_pdfplumber(path: str) -> PdfReadResult | None:
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001
        return None

    name = Path(path).name
    page_tables: list[tuple[int, list[list[object]]]] = []
    texts: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages[:MAX_PAGES]):
                page_text = (page.extract_text() or "").strip()
                if page_text:
                    texts.append(page_text)
                tables = _best_tables(page)
                if tables:
                    page_tables.append((i + 1, tables[0]))
                elif page_text:
                    grid = _grid_from_text(page_text)
                    if len(grid) >= 3:
                        page_tables.append((i + 1, grid))
    except Exception:  # noqa: BLE001
        if not texts and not page_tables:
            return None
        sheets = _sheets_from_pages(name, stitch_continuation_tables(page_tables))
        full_text = "\n".join(texts)
        return PdfReadResult(sheets, full_text, scanned=not sheets and not texts)

    sheets = _sheets_from_pages(name, stitch_continuation_tables(page_tables))
    full_text = "\n".join(texts)
    scanned = not full_text and not sheets
    return PdfReadResult(sheets, full_text, scanned=scanned)


def _sheets_from_pages(name: str, pages: list[tuple[int, list[list[object]]]]) -> list[Sheet]:
    sheets: list[Sheet] = []
    for page_no, grid in pages:
        clean = _as_grid(grid) if grid and row_header_score(grid[0]) < 2 else flatten_header_rows(grid)
        if len(clean) >= 2:
            sheets.append(Sheet(name=f"p{page_no}", grid=clean, source=name))
    return sheets


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
    page_tables: list[tuple[int, list[list[object]]]] = []
    try:
        reader = PdfReader(path)
        for i, page in enumerate(reader.pages[:MAX_PAGES]):
            page_text = repair_mojibake((page.extract_text() or "")).strip()
            if not page_text:
                continue
            texts.append(page_text)
            grid = _grid_from_text(page_text)
            if len(grid) >= 3:
                page_tables.append((i + 1, grid))
    except Exception:  # noqa: BLE001
        sheets = _sheets_from_pages(name, stitch_continuation_tables(page_tables))
        full_text = "\n".join(texts)
        return PdfReadResult(sheets, full_text, scanned=not sheets and not texts)

    sheets = _sheets_from_pages(name, stitch_continuation_tables(page_tables))
    full_text = "\n".join(texts)
    scanned = not full_text and not sheets
    return PdfReadResult(sheets, full_text, scanned=scanned)


def _grid_from_text(text: str) -> list[list[str]]:
    """Split each line on runs of 2+ spaces (borderless invoices/packing)."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        cells = re.split(r"\s{2,}|\t", line.strip())
        rows.append([c.strip() for c in cells])
    return rows
