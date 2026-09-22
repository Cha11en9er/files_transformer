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

from app.transform.canonical import hs_digits, is_number_like, parse_number
from app.transform.reader import Sheet

MAX_PAGES = 25

_HEADER_HINT = re.compile(
    r"description|qty|quantity|article|art\.|weight|amount|origin|"
    r"manufacturer|package|model|series|netto|brutto|price|code|"
    r"наименован|артикул|арт\.?|количество|кол-во|нетто|брутто|цена|сумма|"
    r"страна|серия|модель|стоимость|ст-сть|происх|фирма|ед\.?\s*изм",
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
        score = row_header_score(row)
        if score >= 2:
            header_count += 1
            continue
        if (
            header_count
            and score >= 1
            and not any(is_number_like(cell) for cell in row if _cell_text(cell))
        ):
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
        sheets = _maybe_add_blob_sheet(name, sheets, full_text)
        return PdfReadResult(sheets, full_text, scanned=not sheets and not texts)

    sheets = _sheets_from_pages(name, stitch_continuation_tables(page_tables))
    full_text = "\n".join(texts)
    sheets = _maybe_add_blob_sheet(name, sheets, full_text)
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
        sheets = _maybe_add_blob_sheet(name, sheets, full_text)
        return PdfReadResult(sheets, full_text, scanned=not sheets and not texts)

    sheets = _sheets_from_pages(name, stitch_continuation_tables(page_tables))
    full_text = "\n".join(texts)
    sheets = _maybe_add_blob_sheet(name, sheets, full_text)
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


_BLOB_SLASH = re.compile(
    r"(?i)(?P<design_no>[A-Z]\d{2}-\d{4})\s*/\s*(?P<article>[A-Z][A-Z0-9][A-Z0-9 .()\-]{1,40}?)"
    r"\s*/"
    r"[^\n]{0,200}?"
    r"(?P<qty>\d{1,4}(?:[.,]\d{2,3})?)\s*MT\.?"
    r"\s*(?P<price>\d+[.,]\d+)\s*(?P<ccy>USD|EUR|GBP|TRY|TL|CNY|RMB|AED|\$|€|£)?"
    r".{0,40}?"
    r"(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+[.,]\d+)\s*(?:USD|EUR|GBP|TRY|TL|\$|€|£)?"
)
_BLOB_GLUED = re.compile(
    r"(?i)(?P<article>[A-Z][A-Z0-9]{2,24})"
    r"(?P<qty>\d{1,3}(?:\.\d{3})+,\d+|\d+[.,]\d+)\s*MT\.?"
    r"\s*(?P<price>\d+[.,]\d+)\s*(?P<ccy>USD|EUR|GBP|TRY|TL|CNY|RMB)?"
    r".{0,90}?"
    r"(?P<amount>\d{1,3}(?:\.\d{3})+,\d+|\d+[.,]\d+)"
)
_BLOB_LETTER = re.compile(
    r"(?im)^(?P<qty>\d{1,4}[.,]\d{2})\s+(?P<article>.+?)\s+"
    r"(?P<price>\d+[.,]\d{2})\s+(?P<amount>\d{1,3}(?:\.\d{3})+,\d{2}|\d+[.,]\d{2})"
    r"\s*(?P<ccy>USD|EUR|GBP|TRY|TL|CNY|RMB|AED|\$)?"
)
_BLOB_HS = re.compile(r"(?i)hs\s*code\s*:?\s*([\d. ]{6,24})")
_BLOB_CCY_MAP = {
    "$": "USD", "€": "EUR", "£": "GBP", "TL": "TRY", "RMB": "CNY",
}


def _blob_ccy(raw: str | None) -> str | None:
    if not raw:
        return None
    token = raw.strip().upper()
    return _BLOB_CCY_MAP.get(token, token if len(token) == 3 else None)


def _table_is_weak(grid: list[list[object]]) -> bool:
    if not grid or len(grid) < 3:
        return True
    numeric_rows = 0
    for row in grid[1:]:
        nums = sum(1 for cell in row if cell not in (None, "") and is_number_like(cell) and not hs_digits(cell))
        if nums >= 2:
            numeric_rows += 1
    return numeric_rows < 2


def goods_grid_from_blob(text: str) -> list[list[object]]:
    """Invoice-as-letter / slash-line PDF text → a rectangular goods table."""
    if not text or len(text) < 20:
        return []
    hs_all = [hs_digits(m) for m in _BLOB_HS.findall(text)]
    hs_all = [h for h in hs_all if h]
    rows: list[list[object]] = [
        ["Article", "Meters", "Price", "Amount", "H.S. CODE", "Currency"],
    ]
    seen: set[str] = set()

    def add(article: str, qty: object, price: object, amount: object, ccy: str | None, hs: str | None) -> None:
        name = (article or "").strip(" /-")
        q = parse_number(qty)
        p = parse_number(price)
        a = parse_number(amount)
        if not name or q is None or p is None:
            return
        key = re.sub(r"[^A-Z0-9А-Я]+", "", name.upper())
        if not key or key in seen:
            return
        seen.add(key)
        if a is None:
            a = round(q * p, 2)
        rows.append([name, q, p, a, hs, ccy])

    for match in _BLOB_SLASH.finditer(text):
        article = (match.group("article") or "").strip()
        if article.lower() in {"design name", "desing no", "item no", "new order"}:
            continue
        add(
            article,
            match.group("qty"),
            match.group("price"),
            match.group("amount"),
            _blob_ccy(match.group("ccy")),
            hs_all[0] if hs_all else None,
        )
    for match in _BLOB_GLUED.finditer(text):
        add(
            match.group("article"),
            match.group("qty"),
            match.group("price"),
            match.group("amount"),
            _blob_ccy(match.group("ccy")),
            hs_all[0] if hs_all else None,
        )
    for match in _BLOB_LETTER.finditer(text):
        article = re.sub(r"\s+", " ", match.group("article") or "").strip(" -–—")
        if len(article) < 3 or article.lower().startswith(("total", "hs code")):
            continue
        # Prefer the design token after a dash: "JACQUARD … –LORENSA"
        if "–" in article or "—" in article or " -" in article:
            article = re.split(r"[–—]| -", article)[-1].strip()
        add(
            article,
            match.group("qty"),
            match.group("price"),
            match.group("amount"),
            _blob_ccy(match.group("ccy")),
            hs_all[0] if len(hs_all) == 1 else None,
        )
        if hs_all and len(rows) - 1 <= len(hs_all):
            rows[-1][4] = hs_all[min(len(rows) - 2, len(hs_all) - 1)]
    return rows if len(rows) >= 2 else []


def _maybe_add_blob_sheet(name: str, sheets: list[Sheet], text: str) -> list[Sheet]:
    blob = goods_grid_from_blob(text)
    if len(blob) < 2:
        return sheets
    weak = not sheets or all(_table_is_weak(s.grid) for s in sheets)
    if not weak:
        return sheets
    extra = Sheet(name="text", grid=blob, source=name)
    # Prefer the blob table when plumber only found a totals footer.
    return [extra, *[s for s in sheets if not _table_is_weak(s.grid)]]
