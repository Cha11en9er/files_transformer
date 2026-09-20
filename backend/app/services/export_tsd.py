"""Commercial TSD book: invoice/packing/spec as printed, not the Goldluck ED layout.

Used when goods already carry HS + manufacturer + packages and almost no color
column — the same shape as a finished-goods invoice/packing/specification PDF.
18233 fabric kits and Beijing Goldluck (color/volume/CNY) keep their own writers.
"""

from __future__ import annotations

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

from app.parsing.header_extract import split_party_address
from app.services.export_style import (
    style_data_table,
    style_letterhead_row,
    style_split_letterhead,
    unfreeze_workbook,
)
from app.services.field_map import is_factory_note, parse_number

def _party_address(text: str | None, *, seller: bool) -> str:
    return split_party_address(text, seller=seller) or ""


NUM_FMT = "0.00"
NUM_FMT_4 = "0.0000"
NUM_FMT_INT = "0"


def is_tsd_layout(items: list[dict[str, Any]], header: dict[str, Any] | None = None) -> bool:
    products = [item for item in items if item.get("article")]
    if len(products) < 2:
        return False
    n = len(products)
    hs = 0
    mfr = 0
    color = 0
    volume = 0
    for item in products:
        customs = item.get("customs_data") or {}
        commercial = item.get("commercial_data") or {}
        packing = item.get("packing_data") or {}
        if customs.get("tnved_code") or customs.get("hs_code"):
            hs += 1
        if customs.get("manufacturer"):
            mfr += 1
        if commercial.get("color"):
            color += 1
        if packing.get("volume") not in (None, ""):
            volume += 1
    return hs >= n * 0.6 and mfr >= n * 0.4 and color <= n * 0.25 and volume <= n * 0.25


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
    return str(commercial.get("unit") or packing.get("unit") or "ШТ")


def _packages(item: dict[str, Any]) -> Any:
    packing = item.get("packing_data") or {}
    return packing.get("rolls") or packing.get("boxes")


def _currency(items: list[dict[str, Any]], header: dict[str, Any] | None) -> str:
    for item in items:
        code = str((item.get("commercial_data") or {}).get("currency") or "").upper()
        if code:
            return code
    blob = " ".join(str(v) for v in (header or {}).values())
    low = blob.lower()
    if "cny" in low or "rmb" in low or "юан" in low:
        return "CNY"
    if "eur" in low:
        return "EUR"
    return "USD"


def _hs(item: dict[str, Any]) -> str:
    customs = item.get("customs_data") or {}
    return str(customs.get("tnved_code") or customs.get("hs_code") or "")


def _desc(item: dict[str, Any]) -> str:
    customs = item.get("customs_data") or {}
    en = customs.get("description_en")
    ru = customs.get("description_ru")
    if en and ru and not is_factory_note(en) and not is_factory_note(ru):
        return f"{en} / {ru}"
    for key in ("description", "description_en", "description_ru"):
        value = customs.get(key)
        if value and not is_factory_note(value):
            return str(value)
    return ""


def _ru_desc(item: dict[str, Any]) -> str:
    customs = item.get("customs_data") or {}
    ru = customs.get("description_ru")
    if ru and not is_factory_note(ru):
        return str(ru)
    blob = _desc(item)
    if " / " in blob:
        return blob.split(" / ", 1)[-1]
    return blob


def _mfr(item: dict[str, Any], header: dict[str, Any] | None) -> str:
    customs = item.get("customs_data") or {}
    return str(customs.get("manufacturer") or (header or {}).get("manufacturer") or "")


def _country(item: dict[str, Any]) -> str:
    customs = item.get("customs_data") or {}
    return str(customs.get("country") or "CN")


def _net(item: dict[str, Any]) -> Any:
    return (item.get("packing_data") or {}).get("net_weight")


def _gross(item: dict[str, Any]) -> Any:
    return (item.get("packing_data") or {}).get("gross_weight")


def _price(item: dict[str, Any]) -> Any:
    return (item.get("commercial_data") or {}).get("price")


def _amount(item: dict[str, Any]) -> Any:
    return (item.get("commercial_data") or {}).get("amount")


def _append_table(ws, headers: list[str], rows: list[list[Any]]) -> None:
    ws.append(headers)
    header_row = ws.max_row
    start = ws.max_row + 1
    price_cols = {
        i + 1
        for i, title in enumerate(headers)
        if "price" in title.lower() or "цена" in title.lower() or title.upper().startswith("PRICE")
    }
    for row in rows:
        ws.append(row)
    for r in range(start, ws.max_row + 1):
        for cell in ws[r]:
            if type(cell.value) is int:
                cell.number_format = NUM_FMT_INT
            elif isinstance(cell.value, float):
                cell.number_format = NUM_FMT_4 if cell.column in price_cols else NUM_FMT
            if isinstance(cell.value, str) and cell.value.upper().startswith(("TOTAL", "ИТОГО")):
                cell.font = Font(bold=True)
    style_data_table(
        ws,
        header_row=header_row,
        cols=len(headers),
        n_rows=1 + len(rows),
        headers=headers,
    )


def _header_parties(header: dict[str, Any]) -> tuple[str, str, str, str]:
    header = header or {}
    mixed = header.get("seller_address") or ""
    seller_address = _party_address(mixed, seller=True)
    buyer_address = _party_address(header.get("buyer_address") or mixed, seller=False)
    return (
        header.get("seller") or "",
        seller_address,
        header.get("buyer") or "",
        buyer_address,
    )


def _write_invoice_letterhead(ws, header: dict[str, Any], cols: int) -> None:
    header = header or {}
    seller, seller_address, buyer, buyer_address = _header_parties(header)
    invoice_no = header.get("invoice_no") or ""
    date = header.get("invoice_date") or header.get("date") or ""
    contract = header.get("contract_no") or ""
    contract_date = header.get("contract_date") or ""
    delivery = header.get("delivery_terms") or ""
    if seller:
        ws.append([seller])
        style_letterhead_row(ws, ws.max_row, cols, company=True)
    ws.append([])
    split = min(7, cols)
    pad = [None] * max(split - 2, 0)
    ws.append(["INVOICE:", *pad, invoice_no])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split)
    ws.append(["DATE:", *pad, date])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split)
    ws.append([])
    ws.append(["THE SELLER:"])
    style_letterhead_row(ws, ws.max_row, cols)
    ws.append([seller, *pad, "THE DELIVERY BASIS"])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split)
    contract_line = f"CONTRACT: {contract}".strip()
    if contract_date:
        contract_line = f"{contract_line} dd {contract_date}"
    ws.append([f"Address: {seller_address}".strip(), *pad, contract_line])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    ws.row_dimensions[ws.max_row].height = 36
    spec_line = f"SPECIFICATIONS from {invoice_no}".strip()
    if date:
        spec_line = f"{spec_line} dd {date}"
    ws.append([None, *pad, spec_line])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    ws.append([])
    ws.append(["THE BUYER:", *pad, "RECIPIENT:"])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split)
    ws.append([buyer, *pad, buyer])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    ws.append([f"Address: {buyer_address}".strip(), *pad, f"Address: {buyer_address}".strip()])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    ws.row_dimensions[ws.max_row].height = 36
    if delivery:
        ws.append([f"Terms of delivery: {delivery}"])
        style_letterhead_row(ws, ws.max_row, cols)
    ws.append([])


def _write_packing_letterhead(ws, header: dict[str, Any], cols: int) -> None:
    header = header or {}
    seller, seller_address, buyer, buyer_address = _header_parties(header)
    invoice_no = header.get("invoice_no") or ""
    date = header.get("invoice_date") or header.get("date") or ""
    contract = header.get("contract_no") or ""
    contract_date = header.get("contract_date") or ""
    delivery = header.get("delivery_terms") or ""
    container = header.get("container_no") or header.get("container") or ""
    if seller:
        ws.append([seller])
        style_letterhead_row(ws, ws.max_row, cols, company=True)
    ws.append(["PACKING LIST"])
    style_letterhead_row(ws, ws.max_row, cols, title=True)
    ws.append([f"TO INVOICE: {invoice_no}".strip()])
    style_letterhead_row(ws, ws.max_row, cols)
    ws.append([f"DATE: {date}".strip()])
    style_letterhead_row(ws, ws.max_row, cols)
    ws.append([])
    split = min(5, cols)
    ws.append(["THE SELLER:", None, "THE BUYER:", None, "RECIPIENT:"])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split)
    ws.append([seller, None, buyer, None, buyer])
    style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    ws.append(
        [
            f"Address: {seller_address}".strip(),
            None,
            f"Address: {buyer_address}".strip(),
            None,
            f"Address: {buyer_address}".strip(),
        ]
    )
    style_split_letterhead(ws, ws.max_row, cols, split_at=split, bold=False)
    ws.row_dimensions[ws.max_row].height = 48
    contract_line = f"CONTRACT: {contract}".strip()
    if contract_date:
        contract_line = f"{contract_line} dd {contract_date}"
    ws.append([contract_line])
    style_letterhead_row(ws, ws.max_row, cols)
    if container:
        ws.append([f"CONTAINER: {container}"])
        style_letterhead_row(ws, ws.max_row, cols)
    if delivery:
        ws.append([f"Terms of delivery: {delivery}"])
        style_letterhead_row(ws, ws.max_row, cols)
    ws.append([])


def _write_spec_letterhead(ws, header: dict[str, Any], cols: int) -> None:
    header = header or {}
    invoice_no = header.get("invoice_no") or ""
    contract = header.get("contract_no") or ""
    contract_date = header.get("contract_date") or ""
    date = header.get("invoice_date") or header.get("date") or ""
    buyer = header.get("buyer") or ""
    seller = header.get("seller") or ""
    ws.append([f"Приложение № {invoice_no}".strip()])
    style_letterhead_row(ws, ws.max_row, cols, title=True)
    contract_line = f"к контракту № {contract}".strip()
    if contract_date:
        contract_line = f"{contract_line} от {contract_date}"
    ws.append([contract_line])
    style_letterhead_row(ws, ws.max_row, cols)
    if date:
        ws.append([date])
        style_letterhead_row(ws, ws.max_row, cols)
    if buyer or seller:
        ws.append([f"{buyer} / {seller}".strip(" /")])
        style_letterhead_row(ws, ws.max_row, cols)
        ws.row_dimensions[ws.max_row].height = 36
    ws.append(["1. Продавец продает, Покупатель покупает, а Получатель принимает товар согласно следующей спецификации:"])
    style_letterhead_row(ws, ws.max_row, cols)
    ws.append([])


def invoice_headers(ccy: str) -> list[str]:
    return [
        "№",
        "CODE / Код ТН ВЭД",
        "DESCRIPTION / Описание",
        "COUNTRY OF ORIGIN / Страна происхождения",
        "MODEL / SERIES / ART. / Модель, серия, арт.",
        "MANUFACTURER / BRAND / Производитель",
        "WEIGHT NETTO, kg / Вес нетто, кг",
        "NETTO WITH PRIMARY PACKAGING, kg / Нетто с первичной упаковкой, кг",
        "QTY / Кол-во",
        f"PRICE PER {ccy} / Цена за ед., {ccy}",
        "pcs, pcg, set / шт, уп, компл",
        f"AMOUNT, {ccy} / Сумма, {ccy}",
    ]


def packing_headers() -> list[str]:
    return [
        "№",
        "CODE / Код ТН ВЭД",
        "DESCRIPTION / Описание",
        "MANUFACTURER / Производитель",
        "MODEL / SERIES / ART. / Модель, серия, арт.",
        "PACKAGE / Места",
        "QTY / Кол-во",
        "WEIGHT NETTO, kg / Вес нетто, кг",
        "WEIGHT NETTO WITH PRIMARY PACKAGING, kg / Нетто с первичной упаковкой, кг",
        "WEIGHT BRUTTO, kg / Вес брутто, кг",
        "COUNTRY OF ORIGIN / Страна происхождения",
    ]


def spec_headers(ccy: str) -> list[str]:
    return [
        "НАИМЕНОВАНИЕ ТОВАРА / Description of goods",
        "МОДЕЛЬ, СЕРИЯ, АРТ. / Model, series, art.",
        "ФИРМА ПРОИЗВ-ЛЬ / Manufacturer",
        "СТРАНА ПРОИСХ. / Country of origin",
        "КОЛ-ВО / Quantity",
        "ЕД.ИЗМ. / Unit",
        "ВЕС БРУТТО, КГ / Gross weight, kg",
        "ВЕС НЕТТО, КГ / Net weight, kg",
        "ВЕС НЕТТО С ПЕРВИЧНОЙ УПАКОВКОЙ, КГ / Net with primary packing, kg",
        f"СТОИМОСТЬ, {ccy} / Amount, {ccy}",
        "ЕД.ИЗМ. / Unit",
        f"СТ-СТЬ {ccy} / Unit price, {ccy}",
    ]


def invoice_rows(items: list[dict[str, Any]], header: dict[str, Any] | None) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, item in enumerate(items, start=1):
        net = _fnum(_net(item))
        rows.append(
            [
                idx,
                _hs(item),
                _desc(item),
                _country(item),
                item.get("article") or "",
                _mfr(item, header),
                net,
                net,
                _count(_qty(item)),
                _fnum(_price(item), 4),
                _unit(item),
                _fnum(_amount(item)),
            ]
        )
    if rows:
        body = rows[:]
        rows.append(
            [
                "TOTAL :",
                None,
                None,
                None,
                None,
                None,
                _fnum(sum(float(r[6] or 0) for r in body)),
                _fnum(sum(float(r[7] or 0) for r in body)),
                _count(sum(float(r[8] or 0) for r in body)),
                None,
                None,
                _fnum(sum(float(r[11] or 0) for r in body)),
            ]
        )
    return rows


def packing_rows(items: list[dict[str, Any]], header: dict[str, Any] | None) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, item in enumerate(items, start=1):
        net = _fnum(_net(item))
        rows.append(
            [
                idx,
                _hs(item),
                _desc(item),
                _mfr(item, header),
                item.get("article") or "",
                _count(_packages(item)),
                _count(_qty(item)),
                net,
                net,
                _fnum(_gross(item)),
                _country(item),
            ]
        )
    if rows:
        body = rows[:]
        rows.append(
            [
                "TOTAL",
                None,
                None,
                None,
                None,
                _count(sum(float(r[5] or 0) for r in body)),
                _count(sum(float(r[6] or 0) for r in body)),
                _fnum(sum(float(r[7] or 0) for r in body)),
                _fnum(sum(float(r[8] or 0) for r in body)),
                _fnum(sum(float(r[9] or 0) for r in body)),
                None,
            ]
        )
    return rows


def spec_rows(items: list[dict[str, Any]], header: dict[str, Any] | None) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in items:
        net = _fnum(_net(item))
        rows.append(
            [
                _desc(item),
                item.get("article") or "",
                _mfr(item, header),
                _country(item),
                _count(_qty(item)),
                _unit(item),
                _fnum(_gross(item)),
                net,
                net,
                _fnum(_price(item), 4),
                _unit(item),
                _fnum(_amount(item)),
            ]
        )
    if rows:
        body = rows[:]
        rows.append(
            [
                "ИТОГО:",
                None,
                None,
                None,
                _count(sum(float(r[4] or 0) for r in body)),
                None,
                _fnum(sum(float(r[6] or 0) for r in body)),
                _fnum(sum(float(r[7] or 0) for r in body)),
                _fnum(sum(float(r[8] or 0) for r in body)),
                None,
                None,
                _fnum(sum(float(r[11] or 0) for r in body)),
            ]
        )
    return rows


def description_headers() -> list[str]:
    return ["№", "Код ТН ВЭД", "Изготовитель / Торговая марка", "Модель / артикул", "Описание"]


def description_rows(items: list[dict[str, Any]], header: dict[str, Any] | None) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, item in enumerate(items, start=1):
        rows.append(
            [
                idx,
                _hs(item),
                _mfr(item, header),
                item.get("article") or "",
                _ru_desc(item) or _desc(item),
            ]
        )
    return rows


def dt_headers() -> list[str]:
    return [
        "Код ТН ВЭД",
        "Описание",
        "Описание в группе",
        "Изготовитель",
        "артикул",
        "кол-во товара",
        "Ед.изм.",
        "цена за ед. товара",
        "кол-во мест",
    ]


def dt_rows(items: list[dict[str, Any]], header: dict[str, Any] | None) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in items:
        rows.append(
            [
                _hs(item),
                _desc(item),
                _ru_desc(item) or _desc(item),
                _mfr(item, header),
                item.get("article") or "",
                _count(_qty(item)),
                _unit(item),
                _fnum(_price(item), 4),
                _count(_packages(item)),
            ]
        )
    if rows:
        body = rows[:]
        rows.append(
            [
                None,
                None,
                None,
                None,
                None,
                _count(sum(float(r[5] or 0) for r in body)),
                None,
                None,
                _count(sum(float(r[8] or 0) for r in body)),
            ]
        )
    return rows


def export_tsd_book(items: list[dict[str, Any]], output_path, header: dict[str, Any] | None = None):
    header = header or {}
    ccy = _currency(items, header)
    wb = Workbook()
    ws = wb.active
    ws.title = "INV"
    inv_h = invoice_headers(ccy)
    _write_invoice_letterhead(ws, header, len(inv_h))
    _append_table(ws, inv_h, invoice_rows(items, header))

    ws_pl = wb.create_sheet("PAK")
    pl_h = packing_headers()
    _write_packing_letterhead(ws_pl, header, len(pl_h))
    _append_table(ws_pl, pl_h, packing_rows(items, header))

    ws_spec = wb.create_sheet("Specification")
    spec_h = spec_headers(ccy)
    _write_spec_letterhead(ws_spec, header, len(spec_h))
    _append_table(ws_spec, spec_h, spec_rows(items, header))

    ws_desc = wb.create_sheet("ОПИСАНИЕ")
    _append_table(ws_desc, description_headers(), description_rows(items, header))

    ws_dt = wb.create_sheet("ДЛЯ ДТ")
    _append_table(ws_dt, dt_headers(), dt_rows(items, header))

    unfreeze_workbook(wb)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


def tsd_preview(items: list[dict[str, Any]], header: dict[str, Any] | None, filename: str) -> dict[str, Any]:
    ccy = _currency(items, header)
    return {
        "filename": filename,
        "sheets": [
            {"title": "INV", "headers": invoice_headers(ccy), "rows": invoice_rows(items, header)},
            {"title": "PAK", "headers": packing_headers(), "rows": packing_rows(items, header)},
            {"title": "Specification", "headers": spec_headers(ccy), "rows": spec_rows(items, header)},
            {"title": "ОПИСАНИЕ", "headers": description_headers(), "rows": description_rows(items, header)},
            {"title": "ДЛЯ ДТ", "headers": dt_headers(), "rows": dt_rows(items, header)},
        ],
    }
