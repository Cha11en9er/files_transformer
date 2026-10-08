"""Excel export workers — Word spec §3.2 / §4.1 / §7.

Profile 18233: three workbooks (invoice, packing, specification with colors).
PDF-описания в первом этапе не формируются.
Profile BEIJING: one workbook with sheets Invoice, Packing list, Specification, Description.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app.parsing.header_extract import (
    currency_from_sources,
    export_header_fields,
)
from app.services.bilingual import bilingual, label
from app.services.column_layout import (
    BEIJING_INVOICE_KEYS,
    BEIJING_PACKING_KEYS,
    BEIJING_SPEC_KEYS,
    ELEMENT_INVOICE_KEYS,
    ELEMENT_PACKING_KEYS,
    ELEMENT_SPEC_KEYS,
    FABRIC_INVOICE_KEYS,
    FABRIC_PACKING_KEYS,
    FABRIC_SPEC_KEYS,
    apply_sheet_layout,
)
from app.services.export_18233_templates import (
    _detect_kit,
    _layout_kit,
    invoice_table_rows,
    is_fabric_layout,
    packing_table_rows,
    preview_headers,
    spec_table_rows,
)
from app.services.export_beijing import (
    description_headers,
    description_rows,
    export_beijing_book,
    invoice_headers,
    invoice_rows,
    packing_headers,
    packing_rows,
    spec_headers,
    spec_rows,
)
from app.services.export_tsd import export_tsd_book, is_tsd_layout, tsd_preview
from app.services.export_style import (
    merge_row,
    safe_export_stem,
    style_data_table,
    style_letterhead_row,
    style_split_letterhead,
    unfreeze_workbook,
)


def _write_sheet(ws, headers: list[str], rows: list[list[Any]]) -> None:
    ws.append(headers)
    for row in rows:
        ws.append(row)


def _product_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    products = [
        item
        for item in items
        if (item.get("source_traces") or {}).get("invoice")
        or (item.get("source_traces") or {}).get("specification")
        or item.get("article")
    ]
    if not products:
        products = list(items)

    def _sort_key(item: dict[str, Any]) -> int:
        traces = item.get("source_traces") or {}
        inv = traces.get("invoice") or {}
        no = inv.get("no")
        try:
            return int(no)
        except (TypeError, ValueError):
            return 999

    products.sort(key=_sort_key)
    return products


def _kit_label(items: list[dict[str, Any]], header: dict[str, Any] | None) -> str:
    kit = _detect_kit(items, header)
    if kit:
        return kit
    inv = str((header or {}).get("invoice_no") or "")
    # Хвост после дефиса — код комплекта (626-1). Номер без дефиса комплектом не является.
    if "-" in inv:
        tail = inv.split("-")[-1].strip()
        if tail and tail.lower() != "all" and tail != inv:
            return tail
    return ""


def _aligned_invoice_rows(
    items: list[dict[str, Any]],
    fabric: bool = True,
    header: dict[str, Any] | None = None,
    column_layout: dict[str, Any] | None = None,
) -> tuple[list[str], list[list[Any]]]:
    headers = preview_headers("626-1" if fabric else "18312", items, header)["invoice"]
    keys = FABRIC_INVOICE_KEYS if fabric else ELEMENT_INVOICE_KEYS
    headers, _keys, rows = apply_sheet_layout(
        headers, keys, invoice_table_rows(items, fabric), items, column_layout, "invoice"
    )
    return headers, rows


def _aligned_packing_rows(
    items: list[dict[str, Any]],
    fabric: bool = True,
    header: dict[str, Any] | None = None,
    column_layout: dict[str, Any] | None = None,
) -> tuple[list[str], list[list[Any]]]:
    headers = preview_headers("626-1" if fabric else "18312", items, header)["packing"]
    keys = FABRIC_PACKING_KEYS if fabric else ELEMENT_PACKING_KEYS
    headers, _keys, rows = apply_sheet_layout(
        headers, keys, packing_table_rows(items, fabric), items, column_layout, "packing"
    )
    return headers, rows


def _aligned_spec_rows(
    items: list[dict[str, Any]],
    fabric: bool = True,
    header: dict[str, Any] | None = None,
    column_layout: dict[str, Any] | None = None,
) -> tuple[list[str], list[list[Any]]]:
    headers = preview_headers("626-1" if fabric else "18312", items, header)["specification"]
    keys = FABRIC_SPEC_KEYS if fabric else ELEMENT_SPEC_KEYS
    headers, _keys, rows = apply_sheet_layout(
        headers, keys, spec_table_rows(items, fabric), items, column_layout, "specification"
    )
    return headers, rows


def build_export_preview(
    items: list[dict[str, Any]],
    *,
    profile_type: str,
    header: dict[str, Any] | None = None,
    shipment_title: str = "export",
    column_layout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JSON preview of files that export() will write. No PDF/txt."""
    products = _product_items(items)
    header = prepare_export_header(products, header)
    if str(profile_type).upper() in {"BEIJING", "PROFILETYPE.BEIJING"}:
        stem = safe_export_stem(shipment_title)
        if is_tsd_layout(products):
            return {
                "profile_type": "BEIJING",
                "files": [tsd_preview(products, header, f"ТСД {stem}.xlsx", column_layout)],
            }
        ccy = currency_from_sources(products, header) or ""
        inv_h, _k, inv_r = apply_sheet_layout(
            invoice_headers(ccy), BEIJING_INVOICE_KEYS, invoice_rows(products), products, column_layout, "invoice"
        )
        pl_h, _k, pl_r = apply_sheet_layout(
            packing_headers(), BEIJING_PACKING_KEYS, packing_rows(products), products, column_layout, "packing"
        )
        spec_h, _k, spec_r = apply_sheet_layout(
            spec_headers(ccy), BEIJING_SPEC_KEYS, spec_rows(products), products, column_layout, "specification"
        )
        return {
            "profile_type": "BEIJING",
            "files": [
                {
                    "filename": f"{stem} для ЭД.xlsx",
                    "sheets": [
                        {"title": "Invoice", "headers": inv_h, "rows": inv_r},
                        {"title": "Packing list", "headers": pl_h, "rows": pl_r},
                        {"title": "Specification", "headers": spec_h, "rows": spec_r},
                        {"title": "описание", "headers": description_headers(), "rows": description_rows(products, header)},
                    ],
                }
            ],
        }

    kit = _kit_label(products, header)
    layout = _layout_kit(kit, products, header)
    fabric = is_fabric_layout(layout, products)
    suffix = f" {kit}" if kit else ""
    stem = safe_export_stem(shipment_title)
    inv_h, inv_r = _aligned_invoice_rows(products, fabric, header, column_layout)
    pl_h, pl_r = _aligned_packing_rows(products, fabric, header, column_layout)
    spec_h, spec_r = _aligned_spec_rows(products, fabric, header, column_layout)
    return {
        "profile_type": "18233",
        "files": [
            {
                "filename": f"{stem} ИНВОЙС{suffix}.xlsx",
                "sheets": [{"title": "invoice", "headers": inv_h, "rows": inv_r}],
            },
            {
                "filename": f"{stem} ПАКИНГ{suffix}.xlsx",
                "sheets": [{"title": "packing", "headers": pl_h, "rows": pl_r}],
            },
            {
                "filename": f"{stem}{suffix} СПЕЦИФИКАЦИЯ С ЦВЕТАМИ.xlsx",
                "sheets": [{"title": "specification", "headers": spec_h, "rows": spec_r}],
            },
        ],
    }


def _pad_row(cols: int, *pairs: tuple[int, Any]) -> list[Any]:
    row: list[Any] = [None] * max(cols, 1)
    for idx, value in pairs:
        if 1 <= idx <= len(row):
            row[idx - 1] = value
    return row


def _append_right_pair(ws, left: Any, label: str, value: Any, cols: int) -> None:
    """Left text + label/value in the last two table columns (INV.NO / DATE)."""
    row = [None] * max(cols, 1)
    row[0] = left
    if cols >= 2:
        row[cols - 2] = label
        row[cols - 1] = value
    ws.append(row)
    if cols >= 4:
        merge_row(ws, ws.max_row, 1, cols - 2)
    ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=True, vertical="center")


def _write_letterhead(ws, kind: str, header: dict[str, Any] | None, cols: int = 10) -> None:
    header = header or {}
    seller = header.get("seller") or ""
    buyer = header.get("buyer") or ""
    contract = header.get("contract_no") or ""
    contract_date = header.get("contract_date") or ""
    invoice_no = header.get("invoice_no") or ""
    date = header.get("invoice_date") or header.get("date") or ""
    delivery = header.get("delivery_terms") or ""
    payment = header.get("payment_terms") or ""
    manufacturer = header.get("manufacturer") or ""
    buyer_address = header.get("buyer_address") or ""
    seller_address = header.get("seller_address") or ""
    warehouse = header.get("warehouse_address") or ""
    delivery_date = header.get("delivery_date") or ""
    title = {
        "invoice": bilingual("INVOICE", "ИНВОЙС"),
        "packing": bilingual("PACKING LIST", "УПАКОВОЧНЫЙ ЛИСТ"),
        "specification": bilingual("SPECIFICATION", "СПЕЦИФИКАЦИЯ"),
    }.get(kind, kind.upper())
    if seller:
        ws.append([seller])
        style_letterhead_row(ws, ws.max_row, cols, company=True)
    if seller_address and kind != "packing":
        ws.append([seller_address])
        style_letterhead_row(ws, ws.max_row, cols)
    ws.append([])
    if kind == "specification":
        spec_title = f"{bilingual('SPECIFICATION', 'СПЕЦИФИКАЦИЯ')} №{invoice_no}".strip()
        if date:
            spec_title = f"{spec_title} dd {date}"
        ws.append([spec_title])
        style_letterhead_row(ws, ws.max_row, cols, title=True)
    else:
        ws.append([title])
        style_letterhead_row(ws, ws.max_row, cols, title=True)
    ws.append([])
    if kind == "packing":
        _write_packing_letterhead(ws, header, cols)
        return
    split = min(6, cols)
    if contract:
        dated = f" dd {contract_date}" if contract_date else ""
        ws.append([f"{label('contract_word')} №：{contract}{dated}"])
        style_letterhead_row(ws, ws.max_row, cols)
    ws.append([label("buyer", colon=True), None, None, None, None, label("seller", colon=True)])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split)
    ws.append([buyer, None, None, None, None, seller])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    if buyer_address or seller_address:
        ws.append([label("address", colon=True), None, None, None, None, label("address", colon=True)])
        style_split_letterhead(ws, ws.max_row, cols, split_at=split)
        ws.append([buyer_address, None, None, None, None, seller_address])
        style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
        ws.row_dimensions[ws.max_row].height = 36
    if warehouse:
        ws.append([None, None, None, None, None, label("warehouse", colon=True)])
        style_split_letterhead(ws, ws.max_row, cols, split_at=split)
        ws.append([None, None, None, None, None, warehouse])
        style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    if payment:
        ws.append([f"{label('payment', colon=True)} {payment}"])
        style_letterhead_row(ws, ws.max_row, cols)
    if delivery:
        ws.append([f"{label('delivery', colon=True)} {delivery}"])
        style_letterhead_row(ws, ws.max_row, cols)
    if delivery_date:
        ws.append([f"{label('delivery_date', colon=True)} {delivery_date}"])
        style_letterhead_row(ws, ws.max_row, cols)
    if manufacturer:
        ws.append([f"{label('manufacturer', colon=True)} {manufacturer}"])
        style_letterhead_row(ws, ws.max_row, cols)
    ws.append([])
    if kind == "specification":
        spec_row = _pad_row(
            cols,
            (1, f"{label('invoice_no', colon=True)}"),
            (2, invoice_no),
            (cols - 1, f"{label('date', colon=True)}"),
            (cols, date),
        )
        ws.append(spec_row)
    else:
        spec_no = f"{bilingual('SPECIFICATION', 'СПЕЦИФИКАЦИЯ')} № {invoice_no}".strip()
        if date:
            spec_no = f"{spec_no} {date}"
        ws.append([spec_no])
        style_letterhead_row(ws, ws.max_row, cols)
        left_incoterm = delivery or ""
        contract_line = f"{label('contract')} {contract}".strip()
        if contract_date:
            contract_line = f"{contract_line} dd {contract_date}".strip()
        _append_right_pair(ws, left_incoterm, label("inv_no"), invoice_no, cols)
        _append_right_pair(ws, contract_line, label("date", colon=True), date, cols)
    ws.append([])


def _write_packing_letterhead(ws, header: dict[str, Any], cols: int) -> None:
    """Packing keeps the compact source block: one Buyer cell, INV.NO in last columns."""
    buyer = header.get("buyer") or ""
    buyer_address = header.get("buyer_address") or ""
    invoice_no = header.get("invoice_no") or ""
    date = header.get("invoice_date") or header.get("date") or ""
    contract = header.get("contract_no") or ""
    contract_date = header.get("contract_date") or ""
    delivery = header.get("delivery_terms") or ""
    block = buyer
    if buyer_address:
        block = f"{buyer}\n{buyer_address}" if buyer else buyer_address
    if block:
        low = str(block).lower()
        prefix = "" if low.startswith("buyer") or low.startswith("покупател") else f"{label('buyer', colon=True)} "
        ws.append([f"{prefix}{block}"])
        style_letterhead_row(ws, ws.max_row, cols)
        ws.row_dimensions[ws.max_row].height = 48
    contract_line = f"{label('contract')} {contract}".strip() if contract else ""
    if contract and contract_date:
        contract_line = f"{label('contract')} {contract} dd {contract_date}"
    _append_right_pair(ws, delivery, label("inv_no"), invoice_no, cols)
    _append_right_pair(ws, contract_line, label("date", colon=True), date, cols)
    ws.append([])


def _save_simple_book(path: Path, title: str, headers: list[str], rows: list[list[Any]], header: dict[str, Any] | None = None) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Sheet1"
    _write_letterhead(ws, title, header, cols=max(len(headers), 1))
    header_row = ws.max_row + 1
    _write_sheet(ws, headers, rows)
    for row in ws.iter_rows(min_row=ws.max_row - len(rows), max_row=ws.max_row, min_col=1, max_col=len(headers)):
        for cell in row:
            if type(cell.value) is int:
                cell.number_format = "0"
            elif isinstance(cell.value, float):
                cell.number_format = "0.000" if abs(cell.value) >= 100 or (cell.column in {6, 8, 9} and title == "specification") else "0.00"
            if isinstance(cell.value, str) and cell.value.upper().startswith("TOTAL"):
                cell.font = Font(bold=True)
    style_data_table(
        ws,
        header_row=header_row,
        cols=len(headers),
        n_rows=1 + len(rows),
        headers=headers,
        width_range=(10, 15),
    )
    unfreeze_workbook(wb)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def prepare_export_header(
    items: list[dict[str, Any]] | None,
    header: dict[str, Any] | None,
) -> dict[str, Any]:
    """Operator header wins; only blank manufacturer/currency are filled from goods."""
    return export_header_fields(header or {}, items or [])


def export_beijing(
    items: list[dict[str, Any]],
    output_path: Path,
    header: dict[str, Any] | None = None,
    column_layout: dict[str, Any] | None = None,
) -> Path:
    products = _product_items(items)
    header = prepare_export_header(products, header)
    if is_tsd_layout(products):
        stem = output_path.stem
        if "для ЭД" in stem:
            output_path = output_path.with_name(f"ТСД {stem.replace(' для ЭД', '')}.xlsx")
        return export_tsd_book(products, output_path, header, column_layout=column_layout)
    return export_beijing_book(products, output_path, header, column_layout=column_layout)


def export_18233(
    items: list[dict[str, Any]],
    output_dir: Path,
    header: dict[str, Any] | None = None,
    shipment_title: str = "18233",
    column_layout: dict[str, Any] | None = None,
) -> list[Path]:
    """Three Excel files with the same product rows. No txt/PDF stubs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    products = _product_items(items)
    header = prepare_export_header(products, header)
    kit = _kit_label(products, header)
    layout = _layout_kit(kit, products, header)
    fabric = is_fabric_layout(layout, products)
    suffix = f" {kit}" if kit else ""
    stem = safe_export_stem(shipment_title)
    writers = {
        "invoice": lambda: _aligned_invoice_rows(products, fabric, header, column_layout),
        "packing": lambda: _aligned_packing_rows(products, fabric, header, column_layout),
        "specification": lambda: _aligned_spec_rows(products, fabric, header, column_layout),
    }
    paths: list[Path] = []
    names = [
        (f"{stem} ИНВОЙС{suffix}.xlsx", "invoice"),
        (f"{stem} ПАКИНГ{suffix}.xlsx", "packing"),
        (f"{stem}{suffix} СПЕЦИФИКАЦИЯ С ЦВЕТАМИ.xlsx", "specification"),
    ]
    for filename, kind in names:
        headers, rows = writers[kind]()
        paths.append(
            _save_simple_book(
                output_dir / filename,
                kind,
                headers,
                rows,
                header=header,
            )
        )
    return paths
