"""Role tables on the site, hidden columns, and attaching a source column to lots.

Source files keep their own row count. Output lots are after glue: one spec row
can become several lots of the same article. The attach key follows the same
order as lot glue: article plus quantity, then article alone when quantities
sum, then description, then row order.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import uuid4

from app.parsing.normalize import normalize_article
from app.services.bilingual import bilingual, bilingual_title
from app.services.field_map import parse_number


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_number(value)


def _same_qty(left: Any, right: Any) -> bool:
    a, b = _num(left), _num(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= max(0.05, abs(a) * 0.002)


def _item_qty(item: dict[str, Any]) -> float | None:
    commercial = item.get("commercial_data") or {}
    packing = item.get("packing_data") or {}
    if commercial.get("qty") not in (None, ""):
        return _num(commercial.get("qty"))
    return _num(packing.get("meters"))


def _item_article(item: dict[str, Any]) -> str:
    return normalize_article(item.get("article") or item.get("model") or "")


def _item_desc(item: dict[str, Any]) -> str:
    customs = item.get("customs_data") or {}
    text = customs.get("description") or customs.get("description_en") or customs.get("description_ru") or ""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _row_article(row: dict[str, Any]) -> str:
    return normalize_article(row.get("article") or row.get("vendor") or row.get("model") or "")


def _row_qty(row: dict[str, Any]) -> float | None:
    for key in ("qty", "pieces", "meters"):
        value = _num(row.get(key))
        if value is not None:
            return value
    return None


def _row_desc(row: dict[str, Any]) -> str:
    return "".join(ch for ch in str(row.get("description") or "").lower() if ch.isalnum())


def _row_value(row: dict[str, Any], column: str) -> Any:
    raw = row.get("raw") or {}
    if column in raw and raw.get(column) not in (None, ""):
        return raw.get(column)
    if row.get(column) not in (None, ""):
        return row.get(column)
    return None


def attach_column(
    items: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    column: str,
) -> dict[str, Any]:
    """Map one source column onto the glued lot list. Does not invent values."""
    values: list[Any] = [None] * len(items)
    used = [False] * len(items)
    source_placed = [False] * len(source_rows)
    key_used = "order"
    art_count = Counter(_row_article(row) for row in source_rows if _row_article(row))

    def unused() -> list[int]:
        return [index for index, taken in enumerate(used) if not taken]

    def assign(indexes: list[int], value: Any, how: str) -> None:
        nonlocal key_used
        for index in indexes:
            values[index] = value
            used[index] = True
        if key_used == "order":
            key_used = how

    for src_i, row in enumerate(source_rows):
        value = _row_value(row, column)
        if value in (None, ""):
            source_placed[src_i] = True
            continue
        art = _row_article(row)
        qty = _row_qty(row)
        desc = _row_desc(row)
        placed = False
        if art:
            same = [i for i in unused() if _item_article(items[i]) == art]
            eq = [i for i in same if _same_qty(_item_qty(items[i]), qty)]
            if eq:
                assign([eq[0]], value, "article+qty")
                placed = True
            elif same:
                total = sum(_item_qty(items[i]) or 0 for i in same)
                if qty is not None and _same_qty(total, qty):
                    assign(same, value, "article-sum")
                    placed = True
                elif art_count.get(art, 0) == 1:
                    assign(same, value, "article")
                    placed = True
                else:
                    assign([same[0]], value, "article")
                    placed = True
        if not placed and desc:
            same = [i for i in unused() if _item_desc(items[i]) == desc]
            eq = [i for i in same if _same_qty(_item_qty(items[i]), qty)]
            if eq:
                assign([eq[0]], value, "description+qty")
                placed = True
            elif same:
                total = sum(_item_qty(items[i]) or 0 for i in same)
                if qty is not None and _same_qty(total, qty):
                    assign(same, value, "description-sum")
                    placed = True
                elif len(same) == 1:
                    assign(same, value, "description")
                    placed = True
        source_placed[src_i] = placed

    leftover_src = [
        src_i
        for src_i, row in enumerate(source_rows)
        if not source_placed[src_i] and _row_value(row, column) not in (None, "")
    ]
    leftover_items = unused()
    if leftover_src and len(leftover_src) == len(leftover_items):
        for src_i, index in zip(leftover_src, leftover_items):
            values[index] = _row_value(source_rows[src_i], column)
            used[index] = True
            source_placed[src_i] = True

    unmatched_source = sum(1 for placed in source_placed if not placed)

    mapped: dict[str, Any] = {}
    for index, item in enumerate(items):
        if values[index] in (None, ""):
            continue
        mapped[str(item.get("id") or index)] = values[index]
    matched = sum(1 for value in values if value not in (None, ""))
    return {
        "column": column,
        "key": key_used,
        "values": mapped,
        "matched": matched,
        "item_rows": len(items),
        "source_rows": len(source_rows),
        "unmatched_items": max(0, len(items) - matched),
        "unmatched_source": max(0, unmatched_source),
        "column_id": str(uuid4()),
    }


def layout_for(column_layout: dict[str, Any] | None, role: str) -> dict[str, Any]:
    raw = (column_layout or {}).get(role) or {}
    extra = list(raw.get("extra") or [])
    hidden = [str(item) for item in (raw.get("hidden") or [])]
    return {"hidden": hidden, "extra": extra}


def extra_value(item: dict[str, Any] | None, role: str, column_id: str) -> Any:
    if not item:
        return None
    block = (item.get("extra_columns") or {}).get(role) or {}
    return block.get(column_id)


def _row_item(row: list[Any], items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not row:
        return None
    no = row[0]
    if isinstance(no, int) and 1 <= no <= len(items):
        return items[no - 1]
    return None


def apply_sheet_layout(
    headers: list[str],
    keys: list[str],
    rows: list[list[Any]],
    items: list[dict[str, Any]],
    column_layout: dict[str, Any] | None,
    role: str,
) -> tuple[list[str], list[str], list[list[Any]]]:
    """Drop hidden role columns and append operator-added columns at the end."""
    layout = layout_for(column_layout, role)
    hidden = set(layout["hidden"])
    keep = [index for index, key in enumerate(keys) if key not in hidden]
    next_headers = [headers[index] for index in keep]
    next_keys = [keys[index] for index in keep]
    next_rows = [[row[index] if index < len(row) else None for index in keep] for row in rows]
    for extra in layout["extra"]:
        column_id = str(extra.get("id") or "")
        title = bilingual_title(str(extra.get("title") or "").strip() or "Column")
        next_headers.append(title)
        next_keys.append(f"extra:{column_id}")
        for row in next_rows:
            item = _row_item(row, items)
            row.append(extra_value(item, role, column_id))
    return next_headers, next_keys, next_rows


def key_cols(keys: list[str], names: tuple[str, ...]) -> tuple[int, ...]:
    found = []
    for name in names:
        if name in keys:
            found.append(keys.index(name) + 1)
    return tuple(found)


BEIJING_INVOICE_KEYS = [
    "no",
    "hs_code",
    "description",
    "article",
    "color",
    "qty",
    "unit",
    "price",
    "amount",
    "brand",
    "size",
    "hs_alt",
]
BEIJING_PACKING_KEYS = [
    "no",
    "description",
    "article",
    "color",
    "qty",
    "unit",
    "measurement",
    "gross_weight",
    "net_weight",
    "packages",
    "volume",
    "pcs_per_carton",
]
BEIJING_SPEC_KEYS = [
    "no",
    "article",
    "description",
    "packages",
    "package_type",
    "qty",
    "unit",
    "net_weight",
    "gross_weight",
    "price",
    "amount",
    "hs_code",
    "size",
    "brand",
]
BEIJING_DESC_KEYS = ["article", "description", "manufacturer", "country", "brand", "size"]

ELEMENT_INVOICE_KEYS = ["no", "article", "hs_code", "packages", "qty", "unit", "price", "amount"]
FABRIC_INVOICE_KEYS = ["no", "article", "hs_code", "packages", "width", "area", "qty", "unit", "price", "amount"]
ELEMENT_PACKING_KEYS = ["no", "article", "packages", "qty", "unit", "net_weight", "gross_weight"]
FABRIC_PACKING_KEYS = ["no", "article", "gsm", "packages", "qty", "net_weight", "gross_weight", "area"]
ELEMENT_SPEC_KEYS = [
    "no",
    "article",
    "description",
    "packages",
    "hs_code",
    "hs_alt",
    "qty",
    "unit",
    "area",
    "net_weight",
    "gross_weight",
    "price",
    "amount",
    "brand",
    "manufacturer",
    "country",
    "color",
    "size",
]
FABRIC_SPEC_KEYS = [
    "no",
    "article",
    "description",
    "packages",
    "hs_code",
    "hs_alt",
    "qty",
    "width",
    "area",
    "net_weight",
    "gross_weight",
    "price",
    "amount",
]


def site_columns(profile: str, fabric: bool) -> dict[str, list[dict[str, str]]]:
    """Columns the operator table shows for each role. Extra ones are appended in the browser."""
    beijing = str(profile or "").upper() in {"BEIJING", "PROFILETYPE.BEIJING"}
    if beijing:
        return {
            "invoice": [
                _col("article", bilingual("Art No.", "Артикул"), "text"),
                _col("description", bilingual("Description", "Наименование"), "text"),
                _col("hs_code", bilingual("Customs Code", "Таможенный код"), "code"),
                _col("color", bilingual("Color", "Цвет"), "text"),
                _col("qty", bilingual("Quantity", "Количество"), "num"),
                _col("unit", bilingual("Unit", "Ед. изм."), "text"),
                _col("packages", bilingual("Packages", "Места"), "num"),
                _col("price", bilingual("Price", "Цена"), "money"),
                _col("amount", bilingual("Amount", "Сумма"), "money"),
                _col("brand", bilingual("Brand", "Торговая марка"), "text"),
                _col("size", bilingual("Size", "Размер"), "text"),
                _col("hs_alt", bilingual("Customs code 2", "Второй код"), "code"),
            ],
            "packing": [
                _col("article", bilingual("Art No.", "Артикул"), "text"),
                _col("description", bilingual("Description", "Наименование"), "text"),
                _col("color", bilingual("Color", "Цвет"), "text"),
                _col("qty", bilingual("Quantity", "Количество"), "num"),
                _col("unit", bilingual("Unit", "Ед. изм."), "text"),
                _col("measurement", bilingual("Measurement", "Габарит"), "text"),
                _col("gross_weight", bilingual("Gross Wt.", "Брутто"), "num"),
                _col("net_weight", bilingual("Net Wt.", "Нетто"), "num"),
                _col("packages", bilingual("Cartons", "Коробки"), "num"),
                _col("volume", bilingual("Volume", "Объём"), "num"),
                _col("pcs_per_carton", bilingual("Unit per carton", "Шт. в коробке"), "num"),
            ],
            "specification": [
                _col("article", bilingual("Art.", "Артикул"), "text"),
                _col("description", bilingual("Description", "Наименование"), "text"),
                _col("packages", bilingual("Packages", "Места"), "num"),
                _col("package_type", bilingual("Package", "Вид упаковки"), "text"),
                _col("qty", bilingual("Quantity", "Количество"), "num"),
                _col("unit", bilingual("Unit", "Ед. изм."), "text"),
                _col("net_weight", bilingual("Net weight", "Нетто"), "num"),
                _col("gross_weight", bilingual("Gross weight", "Брутто"), "num"),
                _col("price", bilingual("Price", "Цена"), "money"),
                _col("amount", bilingual("Amount", "Сумма"), "money"),
                _col("hs_code", bilingual("Customs code", "Таможенный код"), "code"),
                _col("brand", bilingual("Brand", "Торговая марка"), "text"),
                _col("manufacturer", bilingual("Manufacturer", "Производитель"), "text"),
                _col("country", bilingual("Country", "Страна"), "text"),
                _col("color", bilingual("Color", "Цвет"), "text"),
                _col("size", bilingual("Size", "Размер"), "text"),
            ],
        }
    if fabric:
        return {
            "invoice": [
                _col("article", bilingual("Design", "Дизайн"), "text"),
                _col("hs_code", bilingual("H.S. Code", "Код ТН ВЭД"), "code"),
                _col("packages", bilingual("Rolls", "Рулоны"), "num"),
                _col("width", bilingual("Width", "Ширина"), "num"),
                _col("area", bilingual("Total m²", "Кол-во кв.м"), "num"),
                _col("qty", bilingual("Meters", "Метры"), "num"),
                _col("unit", bilingual("Unit", "Ед. изм."), "text"),
                _col("price", bilingual("Unit Price", "Цена"), "money"),
                _col("amount", bilingual("Amount", "Сумма"), "money"),
            ],
            "packing": [
                _col("article", bilingual("Design", "Дизайн"), "text"),
                _col("gsm", bilingual("G/M", "г/м"), "num"),
                _col("packages", bilingual("Rolls", "Рулоны"), "num"),
                _col("qty", bilingual("Total meters", "Метры"), "num"),
                _col("net_weight", bilingual("Net weight", "Нетто"), "num"),
                _col("gross_weight", bilingual("Gross weight", "Брутто"), "num"),
                _col("area", bilingual("Total m²", "Кол-во кв.м"), "num"),
            ],
            "specification": [
                _col("article", bilingual("Art.", "Артикул"), "text"),
                _col("description", bilingual("Product name", "Наименование товара"), "text"),
                _col("packages", bilingual("Q-Ty Rolls", "Кол-во рулонов"), "num"),
                _col("hs_code", bilingual("HS code", "Код гармонизированной системы"), "code"),
                _col("hs_alt", bilingual("Customs code", "Таможенный код"), "code"),
                _col("qty", bilingual("Q-ty meters", "Кол-во погонных метров"), "num"),
                _col("width", bilingual("Width, m", "Ширина, м"), "num"),
                _col("area", bilingual("Q-ty m2", "Кол-во кв.м"), "num"),
                _col("net_weight", bilingual("N.W, kg", "Вес Нетто, кг"), "num"),
                _col("gross_weight", bilingual("G.W, kg", "Вес Брутто, кг"), "num"),
                _col("price", bilingual("Price per 1 meter", "Цена за 1 пог.метр"), "money"),
                _col("amount", bilingual("Total price", "Цена"), "money"),
            ],
        }
    return {
        "invoice": [
            _col("article", bilingual("Design", "Дизайн"), "text"),
            _col("hs_code", bilingual("H.S. Code", "Код ТН ВЭД"), "code"),
            _col("packages", bilingual("Packages", "Места"), "num"),
            _col("qty", bilingual("Quantity", "Количество"), "num"),
            _col("unit", bilingual("Unit", "Ед. изм."), "text"),
            _col("price", bilingual("Unit Price", "Цена"), "money"),
            _col("amount", bilingual("Amount", "Сумма"), "money"),
            _col("color", bilingual("Color", "Цвет"), "text"),
            _col("brand", bilingual("Brand", "Торговая марка"), "text"),
            _col("size", bilingual("Size", "Размер"), "text"),
        ],
        "packing": [
            _col("article", bilingual("Design", "Дизайн"), "text"),
            _col("packages", bilingual("Packages", "Места"), "num"),
            _col("qty", bilingual("Quantity", "Количество"), "num"),
            _col("unit", bilingual("Unit", "Ед. изм."), "text"),
            _col("net_weight", bilingual("Net weight", "Нетто"), "num"),
            _col("gross_weight", bilingual("Gross weight", "Брутто"), "num"),
            _col("volume", bilingual("Volume", "Объём"), "num"),
            _col("package_type", bilingual("Package", "Вид упаковки"), "text"),
        ],
        "specification": [
            _col("article", bilingual("Art.", "Артикул"), "text"),
            _col("description", bilingual("Product name", "Наименование товара"), "text"),
            _col("packages", bilingual("Q-Ty packages", "Кол-во упаковок"), "num"),
            _col("hs_code", bilingual("HS code", "Код гармонизированной системы"), "code"),
            _col("hs_alt", bilingual("Customs code", "Таможенный код"), "code"),
            _col("qty", bilingual("Q-ty units", "Кол-во единиц"), "num"),
            _col("unit", bilingual("Unit", "Ед. изм."), "text"),
            _col("area", bilingual("Q-ty m2", "Кол-во кв.м"), "num"),
            _col("net_weight", bilingual("N.W, kg", "Вес Нетто, кг"), "num"),
            _col("gross_weight", bilingual("G.W, kg", "Вес Брутто, кг"), "num"),
            _col("price", bilingual("Price per unit", "Цена за единицу"), "money"),
            _col("amount", bilingual("Total price", "Цена"), "money"),
            _col("brand", bilingual("Brand", "Торговая марка"), "text"),
            _col("manufacturer", bilingual("Manufacturer", "Производитель"), "text"),
            _col("country", bilingual("Country of origin", "Страна происхождения"), "text"),
            _col("color", bilingual("Color", "Цвет"), "text"),
            _col("size", bilingual("Size", "Размер"), "text"),
        ],
    }


def _col(key: str, title: str, kind: str) -> dict[str, str]:
    return {"key": key, "title": title, "kind": kind}
