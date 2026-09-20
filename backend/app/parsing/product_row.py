"""Keep signatures / bank details / addresses out of the item table."""

from __future__ import annotations

import re
from typing import Any

from app.parsing.normalize import normalize_text

_JUNK_FRAGMENTS = (
    "директор",
    "director",
    "генеральн",
    "продаж",
    "мехмет",
    "velichko",
    "величко",
    "банк name",
    "bank name",
    "swift",
    "iban",
    "account no",
    "account nr",
    "beneficiary",
    "огрн",
    "инн ",
    "кпп ",
    "post code",
    "zip code",
    "продавец",
    "покупатель",
    "consignee",
    "ship to",
    "bill to",
    "mersis",
    "delivery adress",
    "delivery address",
)

_ADDRESS_FRAGMENTS = (
    " road",
    " street",
    " str.",
    " city",
    " region",
    " floor",
    " room",
    " building",
    " moscow",
    " village",
    " honaz",
    " denizli",
)

_LOT_RE = re.compile(r"^L\d{6,}", re.IGNORECASE)
_ARTIKEL_TOP_RE = re.compile(r"artikel\s*\d+\s*top", re.IGNORECASE)
_PHONE_RE = re.compile(r"^[tf]\.\+?\d", re.IGNORECASE)


def is_junk_text(value: str | None) -> bool:
    text = normalize_text(value).lower()
    if not text:
        return True
    return any(token in text for token in _JUNK_FRAGMENTS)


def is_plausible_article(value: str | None) -> bool:
    text = normalize_text(value)
    if not text or len(text) > 60:
        return False
    low = text.lower()
    if low in {
        "meters",
        "quantity",
        "description",
        "unit",
        "price",
        "amount",
        "total",
        "pcs",
        "qty",
        "rolls",
        "series / art.",
        "series / art",
        "серия, арт.",
        "серия, арт",
        "model / series / art.",
        "model./series / art.",
        "brand",
        "origin",
        "netto, kg",
        "code",
    }:
        return False
    if re.fullmatch(
        r"(?:model|series|art\.?|article|артикул|арт\.?|item|design|code|description|"
        r"qty|quantity|netto|brutto|weight|origin|brand|серия|модель)"
        r"(?:[\s,/]+(?:model|series|art\.?|article|артикул|арт\.?|item|design|code|brand|серия|модель))*\.?",
        low,
    ):
        return False
    if "new order" in low or "total roll" in low or low.startswith("bank") or "+90" in text.replace(" ", ""):
        return False
    if not text[0].isalpha():
        compact = re.sub(r"[\s\-./]", "", text)
        if compact.isdigit():
            if len(compact) < 4:
                return False
        elif not any(ch.isalpha() for ch in compact):
            return False
    if "\\" in text or text.count("*") >= 2:
        return False
    low = text.lower()
    if is_junk_text(text):
        return False
    if _LOT_RE.match(text) or _ARTIKEL_TOP_RE.search(text) or _PHONE_RE.match(text):
        return False
    if any(token in low for token in _ADDRESS_FRAGMENTS):
        return False
    if ":" in text or "@" in text:
        return False
    if " total" in f" {low}" or low.startswith(("total", "итого", "mt. total", "fca total")):
        return False
    noise = sum(1 for tok in (" usd", " try", " tl", " mt.", " exw") if tok in f" {low}")
    if noise >= 2:
        return False
    if len(text.split()) > 6:
        return False
    if len(text.split()) > 3 and any(
        tok in low for tok in (" sets", " pcs", "zinc", "sand black", "matte black")
    ):
        return False
    if not any(ch.isalpha() for ch in text):
        compact = text.replace(" ", "").replace("-", "")
        return compact.isdigit() and len(compact) >= 4
    return True


def numbers_look_like_product(mapped: dict[str, Any]) -> bool:
    for key in ("qty", "rolls", "price", "amount"):
        value = mapped.get(key)
        if isinstance(value, (int, float)) and abs(value) >= 1_000_000_000:
            return False
    amount = mapped.get("amount")
    if isinstance(amount, (int, float)) and abs(amount) >= 100_000_000:
        return False
    return True


def is_product_article(value: str | None) -> bool:
    """Legacy helper used by tests — prefer table_rows.is_usable_row for parsing."""
    text = normalize_text(value)
    if not is_plausible_article(text):
        return False
    if text.endswith(":"):
        return False
    if text.replace(" ", "").isdigit():
        return False
    return text.count(" ") <= 8
