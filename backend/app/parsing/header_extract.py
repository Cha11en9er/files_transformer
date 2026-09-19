"""Pull shipment header fields from Excel previews and PDF text.

TZ: missing header fields stay for the operator; do not silently pick a conflict.
A value is used only if sources agree, or a single labelled hit is unambiguous.
Catalog / справочник files are ignored (old invoices inside them).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.parsing.normalize import normalize_text

HEADER_KEYS = (
    "buyer",
    "seller",
    "contract_no",
    "contract_date",
    "invoice_no",
    "invoice_date",
    "delivery_terms",
    "payment_terms",
    "manufacturer",
    "delivery_date",
    "warehouse_address",
    "container_no",
    "seller_address",
    "buyer_address",
)

_CATALOG_NAME = re.compile(r"сводн|справоч|catalog|\bописание\b", re.I)

_INVOICE_NO = re.compile(
    r"(?:inv\.?\s*no\.?|invoice\s*(?:no\.?|nr\.?|number)|инвойс\s*№|发票(?:号|号码)?)"
    r"[\s|:：.]*([A-Z0-9][A-Z0-9._/-]{2,})",
    re.I,
)
_CONTRACT_NO = re.compile(
    r"(?:contract(?:/контракт)?|контракт)\s*(?:no\.?|№|#)?"
    r"[\s|:：.]*([A-Z0-9][A-Z0-9._/-]{1,})",
    re.I,
)
_CONTRACT_DATED = re.compile(
    r"(?:dd|dated|от|date)[\s|:：.]*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.I,
)
_DATE_VALUE = (
    r"([A-Za-z]{3,9}\.?\s*\d{1,2},?\s*\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})"
)
_DATE_LABEL = re.compile(
    r"(?:invoice\s*date|inv\.?\s*date|(?:^|[\n|])\s*date|tarih)"
    r"[\s|:：.]*" + _DATE_VALUE,
    re.I,
)
_DATE_STEAL = re.compile(r"(?:delivery|b/?l|bill of|etd|eta)\s*$", re.I)
_BUYER = re.compile(
    r"(?:buyer|покупатель|\bto\b)[\s|:：./]*([^\n|]{3,400})",
    re.I,
)
_SELLER = re.compile(
    r"(?:seller|продавец|exporter|\bshipper\b)[\s|:：./]*([^\n|]{3,400})",
    re.I,
)
_PARTY_LABEL_CELL = re.compile(
    r"^(buyer|seller|покупатель|продавец|to)(?:\s*/\s*(?:покупатель|продавец))?\s*[:.：]?\s*(.*)$",
    re.I | re.S,
)
_GRID_VALUE_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:inv\.?\s*no\.?|invoice\s*(?:no\.?|nr\.?|number)|инв(?:ойс)?\s*(?:номер|№)|инв\s*номер)\s*:?$", re.I), "invoice_no"),
    (re.compile(r"^(?:invoice\s*date|дата(?:\s*инв(?:ойса)?)?)\s*:?$", re.I), "invoice_date"),
    (re.compile(r"^date\s*:?$", re.I), "invoice_date"),
    (re.compile(r"^дата\s*:?$", re.I), "invoice_date"),
    (re.compile(r"^(?:container\s*(?:no\.?|number)?|контейнер)\s*:?$", re.I), "container_no"),
)
_ADDRESS_HINT = re.compile(
    r"(?:\d{5,6}|OGRN|ОГРН|TIN|INN|ИНН|KPP|КПП|street|avenue|road|district|region|city|province|china)",
    re.I,
)
_MANUFACTURER = re.compile(
    r"(?:manufacturer|manufactor|производитель)\s*[:.：]?\s*([^\n|]{3,120})",
    re.I,
)
_PAYMENT = re.compile(
    r"(?:terms of payment|условия оплаты)\s*[:.：/]?\s*([^\n]{8,220})",
    re.I,
)
_DELIVERY_DATE = re.compile(
    r"(?:delivery date|сроки поставки)\s*[:.：/]?\s*([^\n]{4,120})",
    re.I,
)
_WAREHOUSE = re.compile(
    r"(?:warehouse address|адрес склада)\s*[:.：]?\s*([^\n]{6,180})",
    re.I,
)
_CONTAINER = re.compile(
    r"(?:container\s*(?:no\.?|number)?|контейнер)[\s|:：.]*"
    r"([A-Z]{4}\s?\d{6,7}(?:\s*/\s*[A-Z0-9]+)?)",
    re.I,
)
_INCOTERMS = re.compile(
    r"\b(EX-WORKS?|EXW|FCA|FOB|CIF|CFR)\b(?:\s+[A-Za-zА-Яа-яЁё/,-]{2,40})?",
    re.I,
)
_COMPANY = re.compile(
    r"([A-Z][A-Z0-9 .,&'’/-]{6,}(?:CO\.,?\s*LTD\.?|LLC|A\.?\s*Ş\.?|A\.S\.|SRL|GMBH|SANAYI[^\n|]{0,40}))",
    re.I,
)
_SKIP_VALUE = re.compile(
    r"^(buyer|seller|date|invoice|contract|add|address|tel|fax|:|-)?$",
    re.I,
)


def is_catalog_filename(filename: str) -> bool:
    """Сводная / справочник / catalog - lookup only, not a goods sheet."""
    from pathlib import Path

    name = Path(str(filename or "").replace("\\", "/")).name
    return bool(_CATALOG_NAME.search(name))


def _is_catalog(filename: str) -> bool:
    return is_catalog_filename(filename)


def _clean_value(raw: str | None) -> str | None:
    text = normalize_text(raw)
    if not text:
        return None
    text = re.sub(r"^(buyer|seller|to|покупатель|продавец)\s*[:.：/]\s*", "", text, flags=re.I)
    text = text.strip(" :：|/")
    if len(text) < 2 or _SKIP_VALUE.match(text):
        return None
    return text


def _normalize_quotes(text: str) -> str:
    return (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("«", '"')
        .replace("»", '"')
    )


def _clean_party(raw: str | None) -> str | None:
    text = _clean_value(raw)
    if not text:
        return None
    text = _normalize_quotes(text.replace("\\n", " "))
    text = re.sub(r"\s+", " ", normalize_text(text)).strip()
    text = re.split(r"\s*/\s*\d{3,}", text, maxsplit=1)[0]
    text = re.split(
        r"\s+(?:\d{5,}|OGRN|ОГРН|TIN|INN|ИНН|KPP|КПП|Address|Add:|Russian Federation|Российск|Italy|Italia|Turkey|T[uü]rkiye|China)",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    return text[:160] if text else None


def _split_party_block(raw: str | None) -> tuple[str | None, str | None]:
    """Buyer cell: name on the first line, postal / OGRN block as address."""
    text = str(raw or "").replace("\\n", "\n")
    text = re.sub(r"^(buyer|seller|to|покупатель|продавец)(?:\s*/\s*(?:покупатель|продавец))?\s*[:.：/]\s*", "", text, flags=re.I)
    if not text.strip():
        return None, None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, None
    first = lines[0]
    postal = re.search(r"(?:^|\s)(\d{5,6}\b.*)$", first)
    glued = re.search(r"(LLC|ООО|LTD\.?|CO\.,?\s*LTD\.?)(\d{5,6}\b.*)", first, re.I)
    if glued:
        name = _clean_party(first[: glued.start(2)].strip(" /|,;"))
        addr_parts = [glued.group(2).strip()] + lines[1:]
    elif postal and postal.start() > 3:
        name = _clean_party(first[: postal.start()].strip(" /|,;"))
        addr_parts = [postal.group(1).strip()] + lines[1:]
    elif len(lines) > 1:
        name = _clean_party(first)
        addr_parts = lines[1:]
    else:
        name = _clean_party(first)
        addr_parts = []
    address = "\n".join(addr_parts).strip() if addr_parts else None
    if address:
        address = re.sub(r"[ \t]+", " ", address)
        address = re.sub(r" *\n *", "\n", address).strip()
    return name, address or None


def _clean_address(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).replace("\\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip(" :：|/")
    if len(text) < 6:
        return None
    return text


def _address_followup(blob: str, end: int) -> str | None:
    chunk = blob[end : end + 500]
    lines: list[str] = []
    for line in chunk.splitlines():
        line = line.strip(" |")
        if not line:
            if lines:
                break
            continue
        if re.match(r"^(buyer|seller|inv\.?|invoice|contract|date|packing|ex-work|no\.|specification)\b", line, re.I):
            break
        if _ADDRESS_HINT.search(line) or lines:
            lines.append(line)
        else:
            break
    return "\n".join(lines).strip() or None


def _clean_invoice_no(raw: str | None) -> str | None:
    text = _clean_value(raw)
    if not text:
        return None
    match = re.search(r"([A-Z0-9][A-Z0-9._/-]{2,})", text, re.I)
    if not match:
        return None
    value = match.group(1).strip(" .")
    if value.upper() in {"NO", "NR", "DATE", "INV"}:
        return None
    return value


def _clean_contract_no(raw: str | None) -> str | None:
    text = _clean_value(raw)
    if not text:
        return None
    text = re.sub(r"^(contract|контракт)\s*(no\.?|№|#)?\s*[:.：]?\s*", "", text, flags=re.I)
    text = re.split(r"\s+(?:dd|dated|от|date)\b", text, maxsplit=1, flags=re.I)[0]
    text = text.strip(" :：/")
    if len(text) < 2 or text.upper() in {"NO", "№"}:
        return None
    return text[:80]


def _hits_from_text(blob: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = defaultdict(list)

    def add(key: str, value: str | None) -> None:
        if key in {"buyer_address", "seller_address", "warehouse_address"}:
            cleaned = _clean_address(value)
        else:
            cleaned = _clean_value(value)
        if cleaned:
            hits[key].append(cleaned)

    for match in _INVOICE_NO.finditer(blob):
        add("invoice_no", _clean_invoice_no(match.group(1)))
    for match in _CONTRACT_NO.finditer(blob):
        add("contract_no", _clean_contract_no(match.group(1)))
    for match in _CONTRACT_DATED.finditer(blob):
        add("contract_date", match.group(1))
    for match in _DATE_LABEL.finditer(blob):
        prefix = blob[max(0, match.start() - 16) : match.start()]
        if _DATE_STEAL.search(prefix):
            continue
        add("invoice_date", match.group(1))
    for match in _BUYER.finditer(blob):
        name, addr = _split_party_block(match.group(1))
        add("buyer", name)
        add("buyer_address", addr or _address_followup(blob, match.end()))
    for match in _SELLER.finditer(blob):
        name, addr = _split_party_block(match.group(1))
        add("seller", name)
        add("seller_address", addr or _address_followup(blob, match.end()))
    for match in _MANUFACTURER.finditer(blob):
        add("manufacturer", _clean_party(match.group(1)))
    for match in _PAYMENT.finditer(blob):
        add("payment_terms", match.group(1)[:220])
    for match in _DELIVERY_DATE.finditer(blob):
        add("delivery_date", match.group(1)[:120])
    for match in _WAREHOUSE.finditer(blob):
        add("warehouse_address", match.group(1)[:180])
    for match in _CONTAINER.finditer(blob):
        add("container_no", match.group(1))
    for match in _INCOTERMS.finditer(blob):
        add("delivery_terms", normalize_text(match.group(0)))
    return hits


def _letterhead_seller(blob: str) -> str | None:
    for line in blob.splitlines()[:40]:
        if _INVOICE_NO.search(line) or _BUYER.search(line):
            continue
        match = _COMPANY.search(line)
        if match:
            name = _clean_party(match.group(1))
            if name and "buyer" not in name.lower():
                return name
    return None


def _next_nonempty(cells: list[str], start: int) -> str | None:
    for value in cells[start:]:
        text = normalize_text(value)
        if text:
            return text
    return None


def _hits_from_grid(rows: list[list[str]]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = defaultdict(list)

    def add(key: str, value: str | None) -> None:
        if key in {"buyer", "seller", "manufacturer"}:
            cleaned = _clean_party(value)
        elif key == "invoice_no":
            cleaned = _clean_invoice_no(value)
        elif key in {"buyer_address", "seller_address", "warehouse_address"}:
            cleaned = _clean_address(value)
        else:
            cleaned = _clean_value(value)
        if cleaned:
            hits[key].append(cleaned)

    for row in rows or []:
        cells = ["" if c is None else str(c) for c in row]
        for i, cell in enumerate(cells):
            if not normalize_text(cell):
                continue
            party = _PARTY_LABEL_CELL.match(cell.strip())
            if party:
                role = party.group(1).lower()
                remainder = party.group(2) or ""
                name, addr = _split_party_block(cell)
                if not name and remainder.strip() == "":
                    nxt = _next_nonempty(cells, i + 1)
                    if nxt:
                        name, addr = _split_party_block(nxt)
                key = "buyer" if role in {"buyer", "покупатель", "to"} else "seller"
                add(key, name)
                add(f"{key}_address", addr)
                continue
            for pattern, key in _GRID_VALUE_LABELS:
                if pattern.match(cell.strip()):
                    add(key, _next_nonempty(cells, i + 1))
                    break
    return hits


def _letterhead_blob(rows: list[list[str]]) -> str:
    lines: list[str] = []
    for row in rows or []:
        parts = [str(c).replace("\\n", "\n").strip() for c in row if str(c or "").strip()]
        if parts:
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def _collapse_hits(
    by_field: dict[str, list[tuple[str, str]]],
    letterheads: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in HEADER_KEYS:
        picked = _pick_agreed(by_field.get(key) or [])
        if picked:
            result[key] = picked
    if "seller" not in result and letterheads:
        agreed_seller = _pick_agreed([(f"letterhead:{i}", name) for i, name in enumerate(letterheads)])
        if agreed_seller:
            result["seller"] = agreed_seller
        elif len({normalize_text(n).lower() for n in letterheads}) == 1:
            result["seller"] = letterheads[0]
    if "manufacturer" not in result and result.get("seller"):
        result["manufacturer"] = result["seller"]
    if result.get("container_no") and "container" not in result:
        result["container"] = result["container_no"]
    return result


def extract_header_from_letterheads(packs: list[tuple[str, list[list[str]]]] | None) -> dict[str, Any]:
    """Header from Excel cells above the table (merged buyer/address/date)."""
    by_field: dict[str, list[tuple[str, str]]] = defaultdict(list)
    letterheads: list[str] = []
    for filename, rows in packs or []:
        if _is_catalog(str(filename)):
            continue
        found = _hits_from_grid(rows)
        blob = _letterhead_blob(rows)
        for key, values in _hits_from_text(blob).items():
            if found.get(key):
                continue
            found.setdefault(key, []).extend(values)
        for key, values in found.items():
            for value in values:
                if value:
                    by_field[key].append((str(filename), value))
        head = _letterhead_seller(blob)
        if head:
            letterheads.append(head)
    return _collapse_hits(by_field, letterheads)


def extract_header_fields(parsed_docs: list[Any] | None) -> dict[str, Any]:
    """Best-effort header from every Excel sheet preview and PDF text."""
    by_field: dict[str, list[tuple[str, str]]] = defaultdict(list)
    letterheads: list[str] = []
    for doc in parsed_docs or []:
        filename = getattr(doc, "filename", None) or (doc.get("filename") if isinstance(doc, dict) else "") or ""
        if _is_catalog(str(filename)):
            continue
        preview = getattr(doc, "text_preview", None) or (doc.get("text_preview") if isinstance(doc, dict) else "") or ""
        blob = str(preview)
        found = _hits_from_text(blob)
        for key, values in found.items():
            for value in values:
                by_field[key].append((str(filename), value))
        head = _letterhead_seller(blob)
        if head:
            letterheads.append(head)
    return _collapse_hits(by_field, letterheads)


def _norm_key(value: str) -> str:
    return re.sub(r"[\s\"“”'.,]", "", normalize_text(value)).lower()


def _pick_agreed(pairs: list[tuple[str, str]]) -> str | None:
    if not pairs:
        return None
    buckets: dict[str, list[str]] = defaultdict(list)
    for _source, value in pairs:
        if value:
            buckets[_norm_key(value)].append(value)
    if not buckets:
        return None
    ranked = sorted(buckets.values(), key=len, reverse=True)
    if len(ranked) == 1:
        return ranked[0][0]
    if len(ranked[0]) >= 2 and len(ranked[0]) > len(ranked[1]):
        return ranked[0][0]
    if len(ranked) > 1 and _norm_key(ranked[0][0]) != _norm_key(ranked[1][0]) and len(ranked[0]) == len(ranked[1]):
        return None
    return ranked[0][0]


def merge_header_fields(base: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    """Keep already filled fields. Incoming only fills gaps. Conflicts stay as base."""
    out = {k: v for k, v in (base or {}).items() if v not in (None, "", [])}
    for key, value in (incoming or {}).items():
        if key not in HEADER_KEYS and key not in {"date", "incoterms", "consignee", "shipper", "container"}:
            continue
        if value in (None, "", []):
            continue
        mapped = "invoice_date" if key == "date" else key
        if mapped in {"container", "container_no"}:
            mapped = "container_no"
        if mapped == "incoterms" and "delivery_terms" not in out:
            mapped = "delivery_terms"
        current = out.get(mapped)
        if not current:
            out[mapped] = value if mapped != "invoice_no" else (_clean_invoice_no(str(value)) or value)
            continue
        if _norm_key(str(current)) != _norm_key(str(value)):
            continue
    return out
