"""Beijing Goldluck export: one workbook, four sheets matching the ED etalon."""

from __future__ import annotations

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

from app.services.export_style import (
    style_data_table,
    style_letterhead_row,
    style_split_letterhead,
    unfreeze_workbook,
)
from app.services.field_map import is_factory_note, parse_number

NUM_FMT = "0.00"
NUM_FMT_4 = "0.0000"
NUM_FMT_6 = "0.000000"
NUM_FMT_INT = "0"


def _fnum(value: Any, digits: int = 2) -> float | None:
    if value in (None, ""):
        return None
    number = parse_number(value)
    if number is None:
        return None
    return round(float(number), digits)


def _count(value: Any) -> int | float | None:
    number = _fnum(value, 2)
    if number is None:
        return None
    if abs(number - round(number)) < 1e-9:
        return int(round(number))
    return number


def _qty(item: dict[str, Any]) -> Any:
    commercial = item.get("commercial_data") or {}
    packing = item.get("packing_data") or {}
    if commercial.get("qty") not in (None, ""):
        return commercial.get("qty")
    return packing.get("meters")


def _unit(item: dict[str, Any]) -> str:
    commercial = item.get("commercial_data") or {}
    packing = item.get("packing_data") or {}
    return str(commercial.get("unit") or packing.get("unit") or "")


def _cartons(item: dict[str, Any]) -> Any:
    packing = item.get("packing_data") or {}
    traces = item.get("source_traces") or {}
    inv = traces.get("invoice") or {}
    return packing.get("boxes") or packing.get("rolls") or inv.get("boxes") or inv.get("cartons")


def _desc(item: dict[str, Any]) -> str:
    customs = item.get("customs_data") or {}
    en = customs.get("description_en")
    ru = customs.get("description_ru")
    if en and ru and not is_factory_note(en) and not is_factory_note(ru):
        return f"{en}/{ru}"
    for key in ("description", "description_en", "description_ru"):
        value = customs.get(key)
        if value and not is_factory_note(value):
            return str(value)
    return ""


def invoice_headers() -> list[str]:
    return [
        "No",
        "Customs Code",
        "Description of the goods",
        "Art No.",
        "Color",
        "Quantity",
        "Unit",
        "Price (CNY)",
        "Amount (CNY)",
    ]


def packing_headers() -> list[str]:
    return [
        "No",
        "Description of the goods",
        "Art No.",
        "Color",
        "Quantity",
        "Unit",
        "Measurement",
        "Gross Wt. (kg)",
        "Net Wt. (kg)",
        "Cartons",
        "Volume (m3)",
        "unit per carton",
    ]


def spec_headers() -> list[str]:
    return [
        "No",
        "Item/ Артикул",
        "Description/ Наименование",
        "Quantity, ctns/ Количество, коробок",
        "CT",
        "Quantity, unit/ Количество, единиц",
        "Unit/Единица измерения",
        "Netto weight, kg/ Вес нетто, кг",
        "GROSS weight, kg/ Вес брутто, кг",
        "Unit Price / Цена за единицу",
        "Amount / Cтоимость",
        "Customs code / Таможенный код",
    ]


def description_headers() -> list[str]:
    return ["Item/ Артикул", "Description/ Наименование", "Manufacturer", "Country"]


def invoice_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, item in enumerate(items, start=1):
        commercial = item.get("commercial_data") or {}
        customs = item.get("customs_data") or {}
        rows.append(
            [
                idx,
                customs.get("tnved_code") or customs.get("hs_code"),
                _desc(item),
                item.get("article") or item.get("normalized_article") or "",
                commercial.get("color"),
                _count(_qty(item)),
                _unit(item),
                _fnum(commercial.get("price"), 4),
                _fnum(commercial.get("amount")),
            ]
        )
    if rows:
        body = rows[:]
        rows.append(
            [
                None,
                None,
                "TOTAL:",
                None,
                None,
                _count(sum(float(r[5] or 0) for r in body)),
                None,
                None,
                _fnum(sum(float(r[8] or 0) for r in body)),
            ]
        )
    return rows


def packing_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, item in enumerate(items, start=1):
        commercial = item.get("commercial_data") or {}
        packing = item.get("packing_data") or {}
        traces = item.get("source_traces") or {}
        inv = traces.get("invoice") or {}
        rows.append(
            [
                idx,
                _desc(item),
                item.get("article") or "",
                commercial.get("color"),
                _count(_qty(item)),
                _unit(item),
                packing.get("measurement") or inv.get("measurement"),
                _fnum(packing.get("gross_weight")),
                _fnum(packing.get("net_weight")),
                _count(_cartons(item)),
                _fnum(packing.get("volume"), 6),
                _count(packing.get("pcs_per_carton") or inv.get("pcs_per_carton")),
            ]
        )
    if rows:
        body = rows[:]
        rows.append(
            [
                None,
                "TOTAL:",
                None,
                None,
                _count(sum(float(r[4] or 0) for r in body)),
                None,
                None,
                _fnum(sum(float(r[7] or 0) for r in body)),
                _fnum(sum(float(r[8] or 0) for r in body)),
                _count(sum(float(r[9] or 0) for r in body)),
                _fnum(sum(float(r[10] or 0) for r in body), 6),
                None,
            ]
        )
    return rows


def spec_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, item in enumerate(items, start=1):
        commercial = item.get("commercial_data") or {}
        packing = item.get("packing_data") or {}
        customs = item.get("customs_data") or {}
        rows.append(
            [
                idx,
                item.get("article") or "",
                _desc(item),
                _count(_cartons(item)),
                "CT",
                _count(_qty(item)),
                _unit(item),
                _fnum(packing.get("net_weight")),
                _fnum(packing.get("gross_weight")),
                _fnum(commercial.get("price"), 4),
                _fnum(commercial.get("amount")),
                customs.get("tnved_code") or customs.get("hs_code"),
            ]
        )
    if rows:
        body = rows[:]
        rows.append(
            [
                "Total/Итого:",
                None,
                None,
                _count(sum(float(r[3] or 0) for r in body)),
                None,
                _count(sum(float(r[5] or 0) for r in body)),
                None,
                _fnum(sum(float(r[7] or 0) for r in body)),
                _fnum(sum(float(r[8] or 0) for r in body)),
                None,
                _fnum(sum(float(r[10] or 0) for r in body)),
                None,
            ]
        )
    return rows


def description_rows(items: list[dict[str, Any]], header: dict[str, Any] | None) -> list[list[Any]]:
    manufacturer = (header or {}).get("manufacturer") or (header or {}).get("seller") or "BEIJING GOLDLUCK CO., LTD"
    country = (header or {}).get("country") or "CN"
    rows: list[list[Any]] = []
    for item in items:
        customs = item.get("customs_data") or {}
        rows.append(
            [
                item.get("article") or "",
                _desc(item),
                customs.get("manufacturer") or manufacturer,
                customs.get("country") or country,
            ]
        )
    return rows


def _write_letterhead(ws, kind: str, header: dict[str, Any] | None, cols: int) -> None:
    header = header or {}
    seller = header.get("seller") or "BEIJING GOLDLUCK CO., LTD"
    address = header.get("seller_address") or ""
    if not header.get("seller") and not address:
        address = "RM.317, NO.33 DENGSHIKOU STREET, DONGCHENG DISTRICT, BEIJING, CHINA"
    buyer = header.get("buyer") or ""
    buyer_address = header.get("buyer_address") or ""
    contract = header.get("contract_no") or ""
    invoice_no = header.get("invoice_no") or ""
    date = header.get("invoice_date") or header.get("date") or ""
    container = header.get("container_no") or header.get("container") or ""
    ws.append([seller])
    style_letterhead_row(ws, ws.max_row, cols, company=True)
    ws.append([address])
    style_letterhead_row(ws, ws.max_row, cols)
    ws.append([])
    if kind == "specification":
        ws.append([f"Specification / Спецификация №: {invoice_no} dated {date}".strip()])
        style_letterhead_row(ws, ws.max_row, cols, title=True)
        ws.append([f"To the contract / К контракту:{contract}".strip()])
        style_letterhead_row(ws, ws.max_row, cols)
    elif kind == "description":
        ws.append([f"DESCRIPTION / Описание №: {invoice_no} dated {date}".strip()])
        style_letterhead_row(ws, ws.max_row, cols, title=True)
        ws.append([f"To the contract / К контракту:{contract}".strip()])
        style_letterhead_row(ws, ws.max_row, cols)
    else:
        title = "Commercial invoice" if kind == "invoice" else "Packing list"
        ws.append([title])
        style_letterhead_row(ws, ws.max_row, cols, title=True)
        split = min(6, cols)
        pad = [None] * max(split - 2, 0)
        ws.append([f"Buyer: {buyer}", *pad, f"Contract No.: {contract}"])
        style_split_letterhead(ws, ws.max_row, cols, split_at=split)
        ws.append([f"Add: {buyer_address}", *pad, f"Invoice No.: {invoice_no}"])
        style_split_letterhead(ws, ws.max_row, cols, split_at=split)
        ws.append([None, *pad, f"Invoice date: {date}"])
        style_split_letterhead(ws, ws.max_row, cols, split_at=split)
        if container:
            ws.append([None, *pad, f"Container No.: {container}"])
            style_split_letterhead(ws, ws.max_row, cols, split_at=split)
    ws.append([])


def _append_table(ws, headers: list[str], rows: list[list[Any]]) -> None:
    ws.append(headers)
    header_row = ws.max_row
    start = ws.max_row + 1
    price_cols = {
        i + 1
        for i, title in enumerate(headers)
        if "price" in title.lower() or "цена" in title.lower()
    }
    vol_cols = {
        i + 1
        for i, title in enumerate(headers)
        if "volume" in title.lower() or "m3" in title.lower()
    }
    for row in rows:
        ws.append(row)
    for r in range(start, ws.max_row + 1):
        for cell in ws[r]:
            if type(cell.value) is int:
                cell.number_format = NUM_FMT_INT
            elif isinstance(cell.value, float):
                if cell.column in vol_cols:
                    cell.number_format = NUM_FMT_6
                elif cell.column in price_cols:
                    cell.number_format = NUM_FMT_4
                else:
                    cell.number_format = NUM_FMT
            if isinstance(cell.value, str) and cell.value.upper().startswith("TOTAL"):
                cell.font = Font(bold=True)
    style_data_table(
        ws,
        header_row=header_row,
        cols=len(headers),
        n_rows=1 + len(rows),
        headers=headers,
    )


def export_beijing_book(items: list[dict[str, Any]], output_path, header: dict[str, Any] | None = None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    _write_letterhead(ws, "invoice", header, len(invoice_headers()))
    _append_table(ws, invoice_headers(), invoice_rows(items))

    ws_pl = wb.create_sheet("Packing list")
    _write_letterhead(ws_pl, "packing", header, len(packing_headers()))
    _append_table(ws_pl, packing_headers(), packing_rows(items))

    ws_spec = wb.create_sheet("Specification")
    _write_letterhead(ws_spec, "specification", header, len(spec_headers()))
    _append_table(ws_spec, spec_headers(), spec_rows(items))

    ws_desc = wb.create_sheet("описание")
    _write_letterhead(ws_desc, "description", header, len(description_headers()))
    _append_table(ws_desc, description_headers(), description_rows(items, header))

    unfreeze_workbook(wb)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path
