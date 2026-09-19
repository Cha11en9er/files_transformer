"""Shared Excel look: bordered table, bold numbers, merged letterhead cells."""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

THIN = Border(
    left=Side(style="thin", color="000000"),
    right=Side(style="thin", color="000000"),
    top=Side(style="thin", color="000000"),
    bottom=Side(style="thin", color="000000"),
)
HEADER_FILL = PatternFill("solid", fgColor="D9E2F3")
HEADER_FONT = Font(bold=True)
TITLE_FONT = Font(bold=True, size=14)
COMPANY_FONT = Font(bold=True, size=12)
EMPHASIS_FONT = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="center")

_GENERIC_TITLES = frozenset({"", "export", "комплект документов", "shipment"})


def is_generic_shipment_title(title: str | None) -> bool:
    return (title or "").strip().casefold() in _GENERIC_TITLES


def resolve_shipment_title(user_title: str | None, header: dict | None = None) -> str:
    """Default export name is the invoice number; a typed title always wins."""
    typed = (user_title or "").strip()
    invoice_no = str((header or {}).get("invoice_no") or "").strip()
    if typed and not is_generic_shipment_title(typed):
        return typed
    if invoice_no:
        return invoice_no
    return typed or "export"


def safe_export_stem(title: str | None, fallback: str = "export") -> str:
    text = "".join(ch if ch.isalnum() or ch in "-_ .,()" else "_" for ch in (title or "")).strip(" ._")
    return (text[:80] or fallback).rstrip(".")


def merge_row(ws: Worksheet, row: int, start_col: int, end_col: int) -> None:
    if end_col <= start_col:
        return
    ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)


def style_letterhead_row(ws: Worksheet, row: int, cols: int, *, title: bool = False, company: bool = False) -> None:
    if cols < 1:
        return
    merge_row(ws, row, 1, cols)
    cell = ws.cell(row, 1)
    if title:
        cell.font = TITLE_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    elif company:
        cell.font = COMPANY_FONT
    else:
        cell.alignment = WRAP


def style_split_letterhead(ws: Worksheet, row: int, cols: int, split_at: int = 6, *, bold: bool = True) -> None:
    """Buyer block left, contract/invoice block right."""
    if cols < 2:
        return
    left_end = min(split_at - 1, cols - 1)
    merge_row(ws, row, 1, max(left_end, 1))
    if split_at <= cols:
        merge_row(ws, row, split_at, cols)
    if bold:
        ws.cell(row, 1).font = EMPHASIS_FONT
        if split_at <= cols:
            ws.cell(row, split_at).font = EMPHASIS_FONT
    ws.cell(row, 1).alignment = WRAP
    if split_at <= cols:
        ws.cell(row, split_at).alignment = WRAP


def _is_total_row(ws: Worksheet, row: int, cols: int) -> bool:
    for c in range(1, cols + 1):
        value = ws.cell(row, c).value
        if isinstance(value, str) and value.strip().upper().startswith(("TOTAL", "ИТОГО")):
            return True
    return False


def _cell_text_width(value: object) -> int:
    if value is None:
        return 0
    return max((len(line) for line in str(value).splitlines()), default=0)


def style_data_table(
    ws: Worksheet,
    *,
    header_row: int,
    cols: int,
    n_rows: int,
    headers: list[str] | None = None,
) -> None:
    if cols < 1 or n_rows < 1:
        return
    last_row = header_row + n_rows - 1
    for r in range(header_row, last_row + 1):
        total = r > header_row and _is_total_row(ws, r, cols)
        for c in range(1, cols + 1):
            cell = ws.cell(r, c)
            cell.border = THIN
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if r == header_row:
                cell.font = HEADER_FONT
                cell.fill = HEADER_FILL
            elif total:
                cell.font = EMPHASIS_FONT
    for c in range(1, cols + 1):
        letter = get_column_letter(c)
        longest = 0
        header = (headers[c - 1] if headers and c - 1 < len(headers) else "") or ""
        longest = max(longest, _cell_text_width(header))
        for r in range(header_row, last_row + 1):
            longest = max(longest, _cell_text_width(ws.cell(r, c).value))
        ws.column_dimensions[letter].width = min(56, max(8, longest + 2))
    ws.auto_filter.ref = None
    ws.freeze_panes = None


def unfreeze_workbook(wb: Workbook) -> None:
    for ws in wb.worksheets:
        ws.freeze_panes = None
