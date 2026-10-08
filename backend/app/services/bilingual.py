"""EN / RU titles for export letterheads and table headers.

The model does not invent translations. Code writes both languages on the way out.
A title that already has a slash and Cyrillic is left as printed.
"""

from __future__ import annotations

import re
from typing import Any

_CYR = re.compile(r"[А-Яа-яЁё]")
_WS = re.compile(r"\s+")


def bilingual(en: str, ru: str) -> str:
    left = _WS.sub(" ", str(en or "")).strip()
    right = _WS.sub(" ", str(ru or "")).strip()
    if not right:
        return left
    if not left:
        return right
    if right.casefold() in left.casefold():
        return left
    if left.casefold() in right.casefold() and _CYR.search(right):
        return right
    return f"{left} / {right}"


def already_bilingual(text: str) -> bool:
    raw = str(text or "")
    if "/" not in raw:
        return False
    return bool(_CYR.search(raw)) and bool(re.search(r"[A-Za-z]", raw))


LABELS: dict[str, tuple[str, str]] = {
    "buyer": ("Buyer", "Покупатель"),
    "seller": ("Seller", "Продавец"),
    "address": ("Address", "Адрес"),
    "add": ("Add.", "Адрес"),
    "contract": ("Contract No.", "№ контракта"),
    "contract_word": ("Contract", "Контракт"),
    "invoice": ("Invoice", "Инвойс"),
    "invoice_no": ("Invoice No.", "№ инвойса"),
    "invoice_date": ("Invoice date", "Дата инвойса"),
    "date": ("Date", "Дата"),
    "inv_no": ("INV.NO.", "№ инвойса"),
    "delivery": ("Terms of delivery", "Условия поставки"),
    "payment": ("Terms of payment", "Условия оплаты"),
    "manufacturer": ("Manufacturer", "Производитель"),
    "recipient": ("Recipient", "Получатель"),
    "warehouse": ("Warehouse address", "Адрес склада"),
    "container": ("Container No.", "№ контейнера"),
    "delivery_date": ("Delivery date", "Срок поставки"),
    "to_contract": ("To the contract", "К контракту"),
    "bank": ("Bank", "Банк"),
    "the_seller": ("The Seller", "Продавец"),
    "the_buyer": ("The Buyer", "Покупатель"),
    "delivery_basis": ("The delivery basis", "Базис поставки"),
    "packing": ("Packing list", "Упаковочный лист"),
    "specification": ("Specification", "Спецификация"),
    "description": ("Description", "Описание"),
    "commercial_invoice": ("Commercial invoice", "Коммерческий инвойс"),
}


def label(key: str, *, colon: bool = False) -> str:
    pair = LABELS.get(key)
    if not pair:
        return key
    text = bilingual(pair[0], pair[1])
    return f"{text}:" if colon else text


# Exact or startswith keys for rewriting a printed title that is English-only.
_TITLE_RU: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^no\.?$", re.I), bilingual("No.", "№")),
    (re.compile(r"^n[oº°]\.?$", re.I), bilingual("No.", "№")),
    (re.compile(r"^design$", re.I), bilingual("Design", "Дизайн")),
    (re.compile(r"^h\.?s\.?\s*code$", re.I), bilingual("H.S. Code", "Код ТН ВЭД")),
    (re.compile(r"^rolls$", re.I), bilingual("Rolls", "Рулоны")),
    (re.compile(r"^packages$", re.I), bilingual("Packages", "Места")),
    (re.compile(r"^quantity$", re.I), bilingual("Quantity", "Количество")),
    (re.compile(r"^width m$", re.I), bilingual("Width, m", "Ширина, м")),
    (re.compile(r"^total m2$", re.I), bilingual("Total m²", "Кол-во кв.м")),
    (re.compile(r"^meters$", re.I), bilingual("Meters", "Метры")),
    (re.compile(r"^total meters$", re.I), bilingual("Total meters", "Кол-во метров")),
    (re.compile(r"^unit mt/piece$", re.I), bilingual("Unit MT/Piece", "Ед. изм.")),
    (re.compile(r"^unit m/pc$", re.I), bilingual("Unit M/Pc", "Ед. изм.")),
    (re.compile(r"^unit$", re.I), bilingual("Unit", "Ед. изм.")),
    (re.compile(r"^g/m$", re.I), bilingual("G/M", "г/м")),
    (re.compile(r"^net weight/kg$", re.I), bilingual("Net weight, kg", "Вес нетто, кг")),
    (re.compile(r"^gross weight/kg$", re.I), bilingual("Gross weight, kg", "Вес брутто, кг")),
    (re.compile(r"^customs code$", re.I), bilingual("Customs Code", "Таможенный код")),
    (re.compile(r"^art no\.?$", re.I), bilingual("Art No.", "Артикул")),
    (re.compile(r"^color$", re.I), bilingual("Color", "Цвет")),
    (re.compile(r"^measurement$", re.I), bilingual("Measurement", "Габарит")),
    (re.compile(r"^gross wt\.?\s*\(kg\)$", re.I), bilingual("Gross Wt. (kg)", "Вес брутто, кг")),
    (re.compile(r"^net wt\.?\s*\(kg\)$", re.I), bilingual("Net Wt. (kg)", "Вес нетто, кг")),
    (re.compile(r"^cartons$", re.I), bilingual("Cartons", "Коробки")),
    (re.compile(r"^volume \(m3\)$", re.I), bilingual("Volume (m³)", "Объём, м³")),
    (re.compile(r"^unit per carton$", re.I), bilingual("Unit per carton", "Шт. в коробке")),
    (re.compile(r"^manufacturer$", re.I), bilingual("Manufacturer", "Производитель")),
    (re.compile(r"^country$", re.I), bilingual("Country", "Страна")),
    (re.compile(r"^brand$", re.I), bilingual("Brand", "Торговая марка")),
    (re.compile(r"^size$", re.I), bilingual("Size", "Размер")),
    (re.compile(r"^description of the goods$", re.I), bilingual("Description of the goods", "Наименование")),
    (re.compile(r"^buyer\s*:?\s*$", re.I), label("buyer", colon=True)),
    (re.compile(r"^seller\s*:?\s*$", re.I), label("seller", colon=True)),
    (re.compile(r"^address\s*:?\s*$", re.I), label("address", colon=True)),
    (re.compile(r"^date\s*:?\s*$", re.I), label("date", colon=True)),
    (re.compile(r"^inv\.?\s*no\.?\s*:?\s*$", re.I), label("inv_no", colon=True)),
    (re.compile(r"^the seller\s*:?\s*$", re.I), label("the_seller", colon=True)),
    (re.compile(r"^the buyer\s*:?\s*$", re.I), label("the_buyer", colon=True)),
    (re.compile(r"^recipient\s*:?\s*$", re.I), label("recipient", colon=True)),
    (re.compile(r"^the delivery basis\s*:?\s*$", re.I), label("delivery_basis")),
    (re.compile(r"^invoice\s*:?\s*$", re.I), label("invoice", colon=True)),
    (re.compile(r"^packing list\s*:?\s*$", re.I), bilingual("Packing list", "Упаковочный лист")),
    (re.compile(r"^commercial invoice\s*:?\s*$", re.I), bilingual("Commercial invoice", "Коммерческий инвойс")),
]


_PREFIX_RU: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^(unit price)\b", re.I), "Unit Price", "Цена"),
    (re.compile(r"^(amount)\b", re.I), "Amount", "Сумма"),
    (re.compile(r"^(terms of payment)\s*:?", re.I), "Terms of payment", "Условия оплаты"),
    (re.compile(r"^(terms of delivery)\s*:?", re.I), "Terms of delivery", "Условия поставки"),
    (re.compile(r"^(delivery date)\s*:?", re.I), "Delivery date", "Срок поставки"),
    (re.compile(r"^(manufacturer)\s*:?", re.I), "Manufacturer", "Производитель"),
    (re.compile(r"^(buyer)\s*:?", re.I), "Buyer", "Покупатель"),
    (re.compile(r"^(seller)\s*:?", re.I), "Seller", "Продавец"),
    (re.compile(r"^(add(?:ress|\.)?)\s*:?", re.I), "Address", "Адрес"),
    (re.compile(r"^(invoice no\.?)\s*:?", re.I), "Invoice No.", "№ инвойса"),
    (re.compile(r"^(invoice date)\s*:?", re.I), "Invoice date", "Дата инвойса"),
    (re.compile(r"^(contract no\.?)\s*:?", re.I), "Contract No.", "№ контракта"),
    (re.compile(r"^(container no\.?)\s*:?", re.I), "Container No.", "№ контейнера"),
    (re.compile(r"^(recipient)\s*:?", re.I), "Recipient", "Получатель"),
    (re.compile(r"^(warehouse address)\s*:?", re.I), "Warehouse address", "Адрес склада"),
    (re.compile(r"^(bank)\s*:?", re.I), "Bank", "Банк"),
    (re.compile(r"^(specification)\b", re.I), "Specification", "Спецификация"),
    (re.compile(r"^(invoice)\b", re.I), "Invoice", "Инвойс"),
    (re.compile(r"^(packing list)\b", re.I), "Packing list", "Упаковочный лист"),
]


def bilingual_title(text: Any) -> Any:
    """Rewrite an English-only header or letterhead label. Values stay untouched."""
    if not isinstance(text, str):
        return text
    raw = text.strip()
    if not raw or already_bilingual(raw):
        return text
    if raw.lstrip().startswith("="):
        return text
    compact = _WS.sub(" ", raw)
    for pattern, replacement in _TITLE_RU:
        if pattern.fullmatch(compact):
            return replacement
    for pattern, en, ru in _PREFIX_RU:
        match = pattern.match(compact)
        if not match:
            continue
        tail = compact[match.end() :].strip()
        head = bilingual(en, ru)
        if compact.endswith(":") or match.group(0).rstrip().endswith(":"):
            if not head.endswith(":"):
                head = f"{head}:"
        if tail:
            return f"{head} {tail}"
        return head
    return text


def rewrite_sheet_labels(ws, *, max_row: int = 40, max_col: int = 22) -> None:
    """Walk the letterhead and the table header row. Data cells are not rewritten."""
    from openpyxl.cell.cell import MergedCell

    last = min(int(max_row or 1), int(ws.max_row or 1))
    cols = min(int(max_col or 1), int(ws.max_column or 1) or 1)
    for row in range(1, last + 1):
        for col in range(1, cols + 1):
            cell = ws.cell(row, col)
            if isinstance(cell, MergedCell):
                continue
            next_value = bilingual_title(cell.value)
            if next_value != cell.value:
                cell.value = next_value
