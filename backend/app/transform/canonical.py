"""Canonical field set and a column-level (not cell-level) classifier.

The whole point: every supplier ships the same handful of facts under different
column titles, in different languages, plus ~10 columns we do not need. We map each
COLUMN (by its header + the statistics of its values) onto one canonical field, then
read rows against that mapping. Deciding per column, not per cell, is what stops
"area landed in amount" and "meters landed in quantity".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.parsing.normalize import normalize_text

# --------------------------------------------------------------------------- #
# Canonical fields
# --------------------------------------------------------------------------- #

# group: id | commercial | packing | customs | service
# kind:  text | number | code | dimension | unit
@dataclass(frozen=True)
class CanonField:
    key: str
    group: str
    kind: str
    title_ru: str
    title_en: str


CANON_FIELDS: tuple[CanonField, ...] = (
    CanonField("article", "id", "text", "Артикул", "Art./Article"),
    CanonField("color", "id", "text", "Цвет", "Color"),
    CanonField("description", "customs", "text", "Наименование товара", "Description of the goods"),
    CanonField("hs_code", "customs", "code", "Код ТН ВЭД (HS)", "HS code"),
    CanonField("customs_code", "customs", "code", "Таможенный код", "Customs code"),
    CanonField("qty", "commercial", "number", "Количество", "Quantity"),
    CanonField("unit", "commercial", "unit", "Единица измерения", "Unit"),
    CanonField("price", "commercial", "number", "Цена за единицу", "Unit price"),
    CanonField("amount", "commercial", "number", "Стоимость", "Amount"),
    CanonField("currency", "commercial", "text", "Валюта", "Currency"),
    CanonField("rolls", "packing", "number", "Кол-во рулонов/мест", "Rolls/Packages"),
    CanonField("boxes", "packing", "number", "Кол-во коробок", "Cartons"),
    CanonField("meters", "packing", "number", "Кол-во метров", "Meters"),
    CanonField("width", "packing", "number", "Ширина, м", "Width, m"),
    CanonField("area", "packing", "number", "Кол-во кв.м", "Area, m2"),
    CanonField("net_weight", "packing", "number", "Вес нетто, кг", "Net weight, kg"),
    CanonField("gross_weight", "packing", "number", "Вес брутто, кг", "Gross weight, kg"),
    CanonField("volume", "packing", "number", "Объём, м3", "Volume, m3"),
    CanonField("measurement", "packing", "dimension", "Размер упаковки", "Measurement"),
    CanonField("pcs_per_carton", "packing", "number", "Штук в коробке", "Unit per carton"),
    CanonField("gm", "packing", "number", "Плотность, г/м", "g/m"),
    CanonField("manufacturer", "customs", "text", "Производитель", "Manufacturer"),
    CanonField("country", "customs", "text", "Страна", "Country"),
)

CANON_BY_KEY: dict[str, CanonField] = {f.key: f for f in CANON_FIELDS}

NUMERIC_FIELDS: frozenset[str] = frozenset(
    f.key for f in CANON_FIELDS if f.kind in {"number"}
)
CODE_FIELDS: frozenset[str] = frozenset(f.key for f in CANON_FIELDS if f.kind == "code")

# --------------------------------------------------------------------------- #
# Multilingual header synonyms.  (substring, weight)  — matched on normalized lower text.
# Higher weight wins ties. Keep specific phrases heavier than bare words.
# --------------------------------------------------------------------------- #

SYNONYMS: dict[str, tuple[tuple[str, float], ...]] = {
    "article": (
        ("art./артикул", 9), ("art no", 8), ("art. no", 8), ("articul", 8), ("артикул", 9),
        ("articolo", 8), ("stok kodu", 8), ("müşteri kodu", 7), ("musteri kodu", 7),
        ("item/ артикул", 9), ("item /", 7), ("item no", 7), ("item code", 7),
        ("货号", 9), ("品号", 8), ("desen adı", 7), ("desen adi", 7), ("pattern", 5),
        ("design", 4), ("дизайн", 4), ("model", 4), ("модель", 4), ("style", 4),
        ("sku", 6), ("art", 3), ("арт", 3), ("item", 3), ("desing", 4),
        ("series / art", 9), ("series/art", 9), ("model / series", 8),
        ("model./series", 8), ("серия, арт", 9), ("модель, серия", 8),
        ("модель,", 6),
    ),
    "color": (
        ("color/цвет", 9), ("colour", 8), ("цвет", 8), ("color", 8), ("renk", 8), ("colore", 8),
    ),
    "description": (
        ("product name / наименование", 10), ("наименование товара", 10),
        ("description/ наименование", 10), ("description of the goods", 10),
        ("name and specification", 9), ("product name", 8), ("наименование", 7),
        ("description", 6), ("описание", 6), ("mal cinsi", 8), ("品名", 8),
        ("规格名称", 8), ("goods", 4),
        # some suppliers put the goods name into remarks; keep weight low so a real
        # Description column always wins when both exist
        ("remarks", 2), ("annotation", 1), ("примечание", 1),
    ),
    "hs_code": (
        ("hs code / код", 10), ("hs-code", 9), ("hs code", 9), ("код hs", 9), ("h.s. code", 9),
        ("h.s code", 9), ("h.s.", 6), ("код гармониз", 9), ("гармонизир", 7), ("hs", 3),
    ),
    "customs_code": (
        ("customs code / таможен", 10), ("таможенный код", 10), ("customs code", 9),
        ("код тнвэд", 9), ("код тн вэд", 9), ("тн вэд", 8), ("тнвэд", 8), ("rf code", 8),
        ("код тн", 7),
    ),
    "qty": (
        ("quantity, unit", 10), ("кол-во единиц", 10), ("quantity", 6), ("qty", 6),
        ("кол-во", 5), ("количество", 5), ("number of skins", 9), ("кол-во шкур", 9),
        ("hides", 8), ("pcs", 5), ("шт", 4), ("miktar", 6), ("adet", 5), ("数量", 7),
        ("pelli", 6),
    ),
    "unit": (
        ("unit/единица", 10), ("единица измерения", 9), ("ед. измер", 8), ("ед.измер", 8),
        ("uom", 7), ("unit", 4), ("birim", 6), ("unità", 6),
        ("ед.изм", 9), ("ед. изм", 9), ("pcs, pcg, set", 8),
    ),
    "price": (
        ("price per meter", 10), ("price per square meter", 10), ("price per 1 meter", 10),
        ("unit price", 9), ("цена за метр", 10), ("цена за единицу", 10), ("цена за кв", 10),
        ("per meter", 8), ("за пог", 8), ("birim fiyat", 9), ("euro/m2", 9), ("€/m2", 9),
        ("$/m", 8), ("单价", 8), ("unit price(rmb)", 10), ("price (cny)", 9),
        ("price per", 8), ("price", 4),
        ("цена", 4), ("fiyat", 5),
        ("стоимость, usd", 9), ("стоимость ед", 8),
    ),
    "amount": (
        ("total price", 9), ("amount (cny)", 10), ("amount(rmb)", 10), ("amount / cтоимость", 10),
        ("amount/ cтоимость", 10), ("ст-сть", 10), ("стоимость", 8), ("сумма", 7), ("amount", 6),
        ("金额", 8), ("tutar", 7), ("line total", 7), ("цена, юан", 9), ("цена, долл", 9),
    ),
    "currency": (("currency", 8), ("валюта", 8), ("ccy", 6)),
    "rolls": (
        ("number of rolls", 10), ("q-ty rolls", 10), ("кол-во рулон", 10), ("количество рулон", 10),
        ("rolls", 6), ("roll", 4), ("рулон", 6), ("packages", 8), ("package", 7),
        ("top adet", 8), ("мест", 4),
        ("pallet", 8), ("pallets", 8), ("паллет", 8), ("палет", 7), ("palet", 7),
    ),
    "boxes": (
        ("quantity, ctns", 10), ("кол-во коробок", 10), ("cartons", 8), ("carton", 7),
        ("ctns", 8), ("короб", 7), ("boxes", 7), ("box", 5), ("件数", 6),
    ),
    "meters": (
        ("q-ty meters", 10), ("кол-во погонных метров", 10), ("кол-во метров", 9),
        ("total meters", 9), ("net metre", 9), ("meters", 6), ("metres", 6), ("metre", 5),
        ("метр", 5), ("пог", 5), ("м.п", 6), ("length", 5),
    ),
    "width": (("widht", 8), ("width", 8), ("ширина", 8), ("genişlik", 8), ("en, m", 6)),
    "area": (
        ("q-ty m2", 10), ("кол-во кв.м", 10), ("total m2", 9), ("площад", 8), ("кв.м", 8),
        ("m2", 6), ("m²", 6), ("sqm", 7), ("mq", 6),
    ),
    "net_weight": (
        ("n.w, kg", 10), ("net weight", 9), ("netto weight", 9), ("вес нетто", 10),
        ("нетто", 7), ("n.w", 7), ("net wt", 8), ("净重", 8), ("net kilogram", 8),
        ("weight netto", 9), ("weight net", 8), ("nett", 5),
        ("netto with primary", 8), ("primary packaging", 6),
        # exact unit-only headers (matched as whole header in _header_score)
        ("=kg", 9), ("=кг", 9),
    ),
    "gross_weight": (
        ("g.w, kg", 10), ("gross weight", 9), ("вес брутто", 10), ("weight brutto", 10),
        ("брутто", 7), ("brutto", 8), ("g.w", 7),
        ("gross wt", 8), ("毛重", 8), ("brüt kilogram", 8), ("brut kilogram", 8),
        ("brutt", 6),
        ("gross weigth", 9), ("gross weigt", 8),  # frequent PDF/OCR typos
    ),
    "volume": (("volume", 8), ("cbm", 8), ("объ", 7), ("м3", 6), ("m3", 6), ("m³", 6)),
    "measurement": (
        ("measurement", 9), ("carton size", 9), ("размер упаковки", 9), ("размер", 6),
        ("габарит", 7),
    ),
    "pcs_per_carton": (
        ("unit per carton", 10), ("pcs per carton", 10), ("шт в коробке", 10), ("qty/ctn", 9),
        ("шт в", 6),
    ),
    "gm": (("g/m", 8), ("g.m", 7), ("gsm", 8), ("г/м", 7), ("плотность", 6)),
    "manufacturer": (
        ("manufacturer", 9), ("производитель", 9), ("изготовитель", 9), ("maker", 7),
        ("üretici", 8), ("фирма произв", 8),
    ),
    "country": (("country", 8), ("страна", 8), ("origin", 7), ("страна происх", 9)),
}

# Headers that are pure noise / index columns → never a data field.
_IGNORE_HEADERS = ("no.", "№", "no", "п/п", "sira", "sıra", "маркировка", "фото")

_DIMENSION_RE = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*[*x×хX]\s*\d+(?:[.,]\d+)?\s*[*x×хX]\s*\d+", re.IGNORECASE)
_CODE_RE = re.compile(r"^\d{6,12}$")
_CURRENCY_RE = re.compile(r"(usd|eur|cny|rmb|try|tl|\$|€|￥|руб)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Number parsing (EU/US aware) — shared by the whole transformer.
# --------------------------------------------------------------------------- #

_US_MONEY = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
# EU thousands need either a comma-decimal ("1.234,56") or 2+ dot-groups ("1.234.567").
# A lone "77.700" is three decimal places (kg/meters), NOT seventy-seven thousand.
_EU_MONEY = re.compile(r"^-?\d{1,3}(\.\d{3})+,\d+$")
_EU_THOUSANDS = re.compile(r"^-?\d{1,3}(\.\d{3}){2,}$")


def parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        if f != f:  # NaN
            return None
        return f
    text = normalize_text(str(value))
    if not text:
        return None
    text = text.replace("\u00a0", "").replace(" ", "")
    # strip a trailing/leading currency or unit token
    text = re.sub(r"(usd|eur|cny|rmb|try|tl|\$|€|￥|mt\.?|m\.?|kg|pcs|шт)$", "", text, flags=re.IGNORECASE)
    if not text:
        return None
    neg = text.startswith("-")
    body = text[1:] if neg else text
    if _US_MONEY.match(body):
        body = body.replace(",", "")
    elif _EU_MONEY.match(body):
        body = body.replace(".", "").replace(",", ".")
    elif _EU_THOUSANDS.match(body):
        body = body.replace(".", "")
    elif body.count(",") == 1 and body.count(".") == 0:
        body = body.replace(",", ".")
    elif body.count(",") > 1 and body.count(".") == 0:
        body = body.replace(",", "")
    try:
        result = float(body)
    except ValueError:
        return None
    return -result if neg else result


def is_number_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    text = normalize_text(str(value))
    if not text or _DIMENSION_RE.match(text):
        return False
    # reject values with letters except a lone currency/unit suffix
    stripped = _CURRENCY_RE.sub("", text).replace("MT.", "").replace("mt.", "")
    if any(ch.isalpha() for ch in stripped):
        return False
    return parse_number(text) is not None


# --------------------------------------------------------------------------- #
# Column classification
# --------------------------------------------------------------------------- #

@dataclass
class ColumnStat:
    index: int
    header: str
    values: list[Any] = field(default_factory=list)

    @property
    def header_norm(self) -> str:
        return _norm_header(self.header)

    def numeric_fraction(self) -> float:
        vals = [v for v in self.values if normalize_text(str(v))]
        if not vals:
            return 0.0
        return sum(1 for v in vals if is_number_like(v)) / len(vals)

    def dimension_fraction(self) -> float:
        vals = [normalize_text(str(v)) for v in self.values if normalize_text(str(v))]
        if not vals:
            return 0.0
        return sum(1 for v in vals if _DIMENSION_RE.match(v)) / len(vals)

    def code_fraction(self) -> float:
        vals = [normalize_text(str(v)).replace(" ", "") for v in self.values if normalize_text(str(v))]
        if not vals:
            return 0.0
        return sum(1 for v in vals if _CODE_RE.match(v)) / len(vals)

    def max_magnitude(self) -> float:
        best = 0.0
        for v in self.values:
            n = parse_number(v)
            if n is not None:
                best = max(best, abs(n))
        return best


def _norm_header(text: str) -> str:
    t = unicodedata.normalize("NFKC", normalize_text(text)).lower()
    return t


def _header_score(field_key: str, header: str) -> float:
    if not header:
        return 0.0
    compact = header.replace(" ", "")
    best = 0.0
    for token, weight in SYNONYMS[field_key]:
        # "=kg" / "=кг" — whole-header match for bare unit columns
        if token.startswith("="):
            exact = token[1:]
            if header.strip() == exact or compact == exact:
                best = max(best, weight + 2)
            continue
        token_c = token.replace(" ", "")
        if token in header or (token_c and token_c in compact):
            # a bit of a bonus for exact-ish match
            score = weight + (2 if header.strip() == token or compact == token_c else 0)
            best = max(best, score)
    return best


def _is_ignore_header(header: str) -> bool:
    h = header.strip()
    return h in _IGNORE_HEADERS or h in {"no", "№", "no.", "n"}


def classify_columns(columns: list[ColumnStat]) -> dict[int, str]:
    """Assign at most one canonical field per column and one column per field.

    Header synonyms decide most columns; value statistics break ties and rescue
    unlabeled columns (measurement dimensions, code columns, currency-scale prices).
    """
    # 1) raw header scores: field -> {col_index: score}
    candidates: dict[str, dict[int, float]] = {key: {} for key in SYNONYMS}
    for col in columns:
        header = col.header_norm
        if _is_ignore_header(header):
            continue
        for field_key in SYNONYMS:
            score = _header_score(field_key, header)
            if score > 0:
                candidates[field_key][col.index] = score

    # 2) value-based rescue for unlabeled columns
    labeled_by_header = {idx for scores in candidates.values() for idx in scores}
    for col in columns:
        if col.index in labeled_by_header or _is_ignore_header(col.header_norm):
            continue
        if col.dimension_fraction() >= 0.5:
            candidates["measurement"].setdefault(col.index, 4.0)
        elif col.code_fraction() >= 0.6:
            candidates["customs_code"].setdefault(col.index, 2.0)

    # 3) disambiguation: apply value affinity as a tie-adjusting bonus
    def affinity(field_key: str, col: ColumnStat) -> float:
        bonus = 0.0
        numeric = col.numeric_fraction()
        if field_key in NUMERIC_FIELDS:
            bonus += 1.5 * numeric
            if numeric < 0.3:
                bonus -= 3.0
        if field_key == "measurement":
            bonus += 4.0 * col.dimension_fraction()
        if field_key in CODE_FIELDS:
            bonus += 2.0 * col.code_fraction()
        if field_key == "price":
            mag = col.max_magnitude()
            if 0 < mag < 500:
                bonus += 1.0
        if field_key == "area" and numeric:
            bonus += 0.3
        if field_key == "description":
            texts = [normalize_text(str(v)) for v in col.values if normalize_text(str(v))]
            if texts:
                short = sum(1 for t in texts if len(t) <= 18 and " " not in t) / len(texts)
                if short >= 0.6:
                    bonus -= 6.0
        return bonus

    col_by_index = {c.index: c for c in columns}
    scored: list[tuple[float, str, int]] = []
    for field_key, per_col in candidates.items():
        for idx, base in per_col.items():
            total = base + affinity(field_key, col_by_index[idx])
            scored.append((total, field_key, idx))
    scored.sort(reverse=True)

    assigned_field: dict[str, int] = {}
    assigned_col: dict[int, str] = {}
    for _total, field_key, idx in scored:
        if field_key in assigned_field or idx in assigned_col:
            continue
        assigned_field[field_key] = idx
        assigned_col[idx] = field_key
    return assigned_col
