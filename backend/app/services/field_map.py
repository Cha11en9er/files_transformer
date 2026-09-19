"""Map heterogeneous Excel column names to canonical fields.

Strict article matching only — column synonyms are for extraction, not for
merging positions by meaning (Word spec §2, §6).
"""

from __future__ import annotations

import re
from typing import Any

from app.parsing.normalize import normalize_text

# Canonical key -> accepted header fragments (lowercase)
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "article": (
        "артикул",
        "articul",
        "article",
        "арт",
        "art.",
        "sku",
        "item no",
        "item code",
        "item",
        "part no",
        "part number",
        "style",
        "design",
        "дизайн",
        "код товара",
        "stok kodu",
        "musteri kodu",
        "müşteri kodu",
        "malzeme",
        "货号",
        "品号",
        "art no",
        "art. no",
        "series / art",
        "series/art",
        "model / series",
        "pattern",
        "desen adı",
        "desen adi",
        "articolo",
    ),
    "model": ("model", "модель", "design", "дизайн", "ürün kodu", "urun kodu"),
    "color": ("color", "colour", "цвет", "renk", "colore"),
    "qty": ("qty", "quantity", "кол-во", "количество", "pcs", "шт", "miktar", "adet", "数量", "件数", "hides", "pelli"),
    "unit": ("unit", "uom", "ед", "единица", "birim", "unità"),
    "price": (
        "unit price",
        "per meter",
        "за пог",
        "price",
        "цена",
        "birim fiyat",
        "fiyat",
        "单价",
        "unit $",
        "euro/m2",
        "€/m2",
    ),
    "amount": ("total price", "amount", "сумма", "tutar", "金额", "line total", "line $"),
    "currency": ("currency", "валюта", "ccy"),
    "rolls": (
        "number of rolls",
        "rolls",
        "roll",
        "рулонов",
        "рулон",
        "packages",
        "package",
        "pkgs",
        "total roll",
        "top adet",
    ),
    "boxes": ("boxes", "box", "cartons", "carton", "короб", "мест"),
    "meters": (
        "погонных метров",
        "meters",
        "metres",
        "meter",
        "пог",
        "м.п",
        "мп",
        "length",
        "net metre",
        "brüt metre",
        "brut metre",
        "total meter",
        "total metres",
    ),
    "area": ("кв.м", "area", "м²", "m2", "sqm", "площад", "mq"),
    "gm": ("g/m", "g.m", "gsm"),
    "width": ("widht", "width", "ширина"),
    "net_weight": (
        "net weight",
        "n.w",
        "nw",
        "нетто",
        "net wt",
        "net kilogram",
        "净重",
        "weight netto",
        "weight net",
        "netto with primary",
        "primary packaging",
    ),
    "gross_weight": (
        "gross weight",
        "g.w",
        "gw",
        "брутто",
        "brutto",
        "weight brutto",
        "gross wt",
        "brüt kilogram",
        "brut kilogram",
        "毛重",
        "brutt",
    ),
    "volume": ("volume", "cbm", "объем", "объём", "м³", "m3"),
    "measurement": ("measurement", "размер", "carton size", "меш"),
    "pcs_per_carton": ("pcs per carton", "qty/ctn", "unit per carton", "шт в"),
    "hs_code": ("hs-code", "hs code", "гармонизир", "hs", "h.s"),
    "tnved_code": ("customs code", "таможенный код", "tn ved", "tnved", "тн вэд", "тнвэд", "код тн"),
    "description_en": ("description en", "description (en)", "desc en", "english"),
    "description_ru": ("description ru", "description (ru)", "desc ru", "russian"),
    "description": (
        "product name / наименование товара",
        "name and specification of commodity",
        "product name",
        "наименование товара",
        "наименование",
        "description",
        "описание",
        "goods",
        "mal cinsi",
        "品名",
        "规格名称",
    ),
    "country": ("country", "страна", "origin"),
    "manufacturer": ("manufacturer", "maker", "изготовитель", "производитель"),
    "brand": ("brand", "trade mark", "марка", "бренд"),
}

_NUMERIC_FIELDS = {
    "qty",
    "price",
    "amount",
    "rolls",
    "boxes",
    "meters",
    "area",
    "width",
    "gm",
    "net_weight",
    "gross_weight",
    "volume",
    "pcs_per_carton",
}


_US_MONEY = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_EU_MONEY = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)$")
_FACTORY_NOTE_RE = re.compile(
    r"^\s*(\([^)]{0,48}\)|[A-Za-z]\s*|\d{3,6}\s+special\s+order)\s*$",
    re.IGNORECASE,
)
_AREA_HEADER_RE = re.compile(
    r"(total\s*)?(m2|m²|sq\.?\s*m|кв\.?\s*м|площад)",
    re.IGNORECASE,
)
_AMOUNT_TOTAL_BLOCK_RE = re.compile(
    r"m2|m²|sqm|meter|metre|weight|кг|kg|roll|qty|quantity|нетто|брутто|width|ширин",
    re.IGNORECASE,
)


def parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = normalize_text(str(value)).replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    if _US_MONEY.match(text):
        text = text.replace(",", "")
    elif _EU_MONEY.match(text):
        text = text.replace(".", "").replace(",", ".")
    elif text.count(",") == 1 and text.count(".") == 0:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def is_numeric_token(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    text = normalize_text(str(value))
    if not text or any(ch.isalpha() for ch in text):
        return False
    return parse_number(text) is not None


def _to_float(value: Any) -> float | None:
    number = parse_number(value)
    if number is None:
        return None
    return round(float(number), 6)


def nearly_equal(left: Any, right: Any, *, tol: float = 0.05) -> bool:
    if left is None or right is None:
        return left is right
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError):
        return str(left).strip().upper() == str(right).strip().upper()
    return abs(a - b) <= max(tol, abs(a) * 0.002)


_SKU_NOTE_RE = re.compile(r"^[A-Za-z0-9]{1,12}(?:[._/-][A-Za-z0-9]{1,12}){1,4}$")


def is_factory_note(value: Any) -> bool:
    """Mill cutting marks like (15+30) or (A), not a customs description."""
    text = normalize_text(str(value or ""))
    if not text or len(text) > 48:
        return False
    if _FACTORY_NOTE_RE.match(text):
        return True
    # Annotation codes such as D680-1 / A519-3 are component notes, not names.
    if " " not in text and _SKU_NOTE_RE.match(text):
        return True
    return False


def repair_line_amount(mapped: dict[str, Any]) -> dict[str, Any]:
    """If Amount was filled from TOTAL M2 / meters, restore price × qty."""
    price = mapped.get("price")
    qty = mapped.get("qty")
    if qty is None:
        qty = mapped.get("meters")
    amount = mapped.get("amount")
    area = mapped.get("area")
    if price in (None, "") or qty in (None, ""):
        return mapped
    try:
        expected = round(float(price) * float(qty), 2)
    except (TypeError, ValueError):
        return mapped
    if amount in (None, ""):
        mapped["amount"] = expected
        return mapped
    try:
        current = float(amount)
    except (TypeError, ValueError):
        mapped["amount"] = expected
        return mapped
    if nearly_equal(current, expected, tol=max(0.05, abs(expected) * 0.02)):
        return mapped
    confused = False
    if area not in (None, "") and nearly_equal(current, area):
        confused = True
    elif qty not in (None, "") and nearly_equal(current, qty):
        confused = True
    else:
        if expected:
            ratio = current / expected
            if abs(ratio - 10) < 0.08 or abs(ratio - 100) < 0.08 or abs(ratio - 0.1) < 0.02:
                confused = True
    if confused:
        mapped["amount"] = expected
    return mapped


def classify_header(header: str) -> str | None:
    """Pick the most specific canonical field for a column title."""
    h = normalize_text(header).lower()
    if not h or h.startswith("column"):
        return None
    if h in COLUMN_ALIASES:
        return h
    if "артикул" in h or "articul" in h:
        return "article"
    if "art no" in h or re.search(r"(^|[^a-z])art\.?\s*no", h):
        return "article"
    if "series / art" in h or "series/art" in h or "model / series" in h:
        return "article"
    if any(
        tok in h
        for tok in (
            "stok kodu",
            "müşteri kodu",
            "musteri kodu",
            "货号",
            "品号",
            "desing",
            "desen adi",
            "desen adı",
        )
    ):
        return "article"
    if re.search(r"(^|[^a-z])pattern([^a-z]|$)", h):
        return "article"
    if "hides" in h or h.strip() in {"pelli", "hide"}:
        return "qty"
    if "mal cinsi" in h or "品名" in h:
        return "description"
    if re.search(r"(^|[^a-z])(design|desen)([^a-z]|$)", h) and "code" not in h:
        return "article"
    if re.search(r"(^|[^a-z])article([^a-z]|$)", h) and "name" not in h:
        return "article"
    if "product name / наименование" in h or "name and specification" in h:
        return "description"
    if re.search(r"(^|[^a-z])product name([^a-z]|$)", h) and "наименование товара" not in h:
        return "article"
    if "package" in h:
        if "weight" in h or "netto" in h or "нетто" in h or "kg" in h:
            return "net_weight"
        return "rolls"
    if "номер рулона" in h or "rulon" in h or "roll no" in h or "roll nr" in h:
        return None
    if "number of rolls" in h or "количество рулон" in h or re.search(r"\brolls\b", h):
        return "rolls"
    if "погонных метров" in h or "q-ty meters" in h or "metres" in h or "meters" in h:
        return "meters"
    if _AREA_HEADER_RE.search(h) and "euro" not in h and "price" not in h and "amount" not in h:
        return "area"
    if h.strip() in {"total", "grand total", "итого", "всего", "合计", "总计"}:
        return "amount"
    if "amount (m)" in h or h.strip() in {"amount (m)", "nett", "brutt"}:
        if "kg" in h or h.strip() in {"nett", "nett kg"}:
            return "net_weight"
        if "brutt" in h or "gross" in h:
            return "gross_weight"
        return "meters"
    if h.strip() in {"код", "тнвэд", "tnved"}:
        return "tnved_code"
    if "nett" in h and "kg" in h:
        return "net_weight"
    if "ширина" in h or "widht" in h or "width" in h:
        return "width"
    if "кв.м" in h or "q-ty m2" in h or "м²" in h or h.strip() == "area":
        return "area"
    if "total price" in h or "цена, долл" in h or "сумма" in h or "tutar" in h or "金额" in h:
        return "amount"
    if "per meter" in h or "за пог" in h or "unit price" in h or "birim fiyat" in h or "单价" in h or "price per" in h:
        return "price"
    if "miktar" in h or "adet" in h or "数量" in h:
        return "qty"
    if h in {"fiyat", "price $", "unit $", "usd"} or (("$" in h or "€" in h) and "total" not in h and "amount" not in h):
        return "price"
    if "netto with primary" in h or "primary packaging" in h:
        return "net_weight"
    if "нетто" in h or "n.w" in h or "net weight" in h or "weight net" in h:
        return "net_weight"
    if "брутто" in h or "brutto" in h or "g.w" in h or "gross weight" in h:
        return "gross_weight"
    if "таможенный код" in h or "customs code" in h or "тн вэд" in h:
        return "tnved_code"
    if "hs-code" in h or "hs code" in h or "гармонизир" in h:
        return "hs_code"
    if "наименование" in h and "артикул" not in h:
        return "description"

    best: tuple[int, str] | None = None
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias not in h:
                continue
            if canonical == "amount" and alias in {"total", "amount"} and _AMOUNT_TOTAL_BLOCK_RE.search(h):
                if alias == "total" or "m2" in h or "m²" in h:
                    continue
            score = len(alias)
            if best is None or score > best[0]:
                best = (score, canonical)
    return best[1] if best else None


def map_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract canonical fields from a raw parsed row dict."""
    mapped: dict[str, Any] = {}
    for key, value in raw.items():
        header = normalize_text(str(key)).lower()
        if not header:
            continue
        canonical = classify_header(header)
        if canonical is None:
            continue
        if canonical in mapped and mapped[canonical] not in (None, ""):
            continue
        if canonical in _NUMERIC_FIELDS:
            mapped[canonical] = _to_float(value)
        else:
            text = normalize_text(str(value)) if value is not None else ""
            if canonical == "description" and is_factory_note(text):
                continue
            mapped[canonical] = text or None
    if not mapped.get("article") and mapped.get("model"):
        mapped["article"] = mapped["model"]
    color = mapped.get("color")
    article = mapped.get("article")
    if article and color and re.fullmatch(r"\d{1,4}", str(color).strip()) and not re.search(r"\d", str(article)):
        mapped["article"] = f"{article} {int(color):02d}"
    return repair_line_amount(mapped)
