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
from app.transform.canonical import normalize_currency_iso

_LOOKALIKE = str.maketrans(
    "АВЕКМНОРСТХавекмнорстх",
    "ABEKMHOPCTXabekmhopctx",
)


def _fold_lookalikes(text: str) -> str:
    return (text or "").translate(_LOOKALIKE)

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
    "currency",
    "delivery_date",
    "warehouse_address",
    "container_no",
    "seller_address",
    "buyer_address",
)

_CATALOG_NAME = re.compile(r"сводн|справоч|catalog|\bописание\b", re.I)

_INVOICE_NO = re.compile(
    r"(?:inv\.?\s*no\.?|invoice\s*(?:no\.?|nr\.?|number)|invoice\s*:|to\s+invoice|"
    r"инвойс\s*№|发票(?:号|号码)?)"
    r"[\s|:：.]*([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9._/-]{2,})",
    re.I,
)
# One cell: "INVOICE DATE/NUMER : 29,04,2026 / ESI2026000000043".
_INVOICE_DATE_NUMER = re.compile(
    r"invoice\s+date\s*/\s*(?:numer|number|no\.?|nr\.?)\s*[:：]?\s*"
    r"\d{1,2}[,./]\d{1,2}[,./]\d{2,4}\s*/\s*"
    r"([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9._/-]{2,})",
    re.I,
)
_CONTRACT_NO = re.compile(
    r"(?:contract(?:/контракт)?|контракт(?:у|а|е)?)\s*(?:no\.?|№|#|:)"
    r"[\s|:：.]*([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9._/-]{1,})",
    re.I,
)
_CONTRACT_DATED = re.compile(
    r"(?:dd|dated|(?<![A-Za-zА-Яа-яЁё])от)[\s|:：.]*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})",
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
    r"(?:(?:the\s+)?buyer|покупатель|to)\s*[:：]\s*([^\n|]{0,400})",
    re.I,
)
_SELLER = re.compile(
    r"(?:(?:the\s+)?seller|продавец|exporter|shipper)\s*[:：]\s*([^\n|]{0,400})",
    re.I,
)
_PARTY_LABEL_CELL = re.compile(
    r"^(?:the\s+)?(buyer|seller|покупатель|продавец|to)(?:\s*/\s*(?:покупатель|продавец))?\s*[:.：]?\s*(.*)$",
    re.I | re.S,
)
_GRID_VALUE_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:inv\.?\s*no\.?|invoice\s*(?:no\.?|nr\.?|number)|инв(?:ойс)?\s*(?:номер|№)|инв\s*номер|invoice)\s*:?$", re.I), "invoice_no"),
    (re.compile(r"^(?:invoice\s*date|дата(?:\s*инв(?:ойса)?)?)\s*:?$", re.I), "invoice_date"),
    (re.compile(r"^date\s*:?$", re.I), "invoice_date"),
    (re.compile(r"^дата\s*:?$", re.I), "invoice_date"),
    (re.compile(r"^(?:container\s*(?:no\.?|number)?|контейнер)\s*:?$", re.I), "container_no"),
    (re.compile(r"^(?:contract(?:\s*(?:no\.?|№|#))?|контракт(?:\s*№)?)\s*:?$", re.I), "contract_no"),
    # Broker blank: "SPECIFICATION/Спецификация №" and the number sits in the next cell.
    (re.compile(r"^(?:specification|спецификац\w*).*№\s*$", re.I), "spec_no"),
)
_ADDRESS_HINT = re.compile(
    r"(?:\d{5,6}|OGRN|ОГРН|TIN|INN|ИНН|KPP|КПП|street|\bst\.|str\.|avenue|road|"
    r"district|region|city|province|china|hong\s*kong|hongkong|address\s*:|"
    r"bldg|building|apt\.?|office|room|ул\.|д\.)",
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
    r"\b(EX-WORKS?|EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP|DAT|DDU)\b"
    r"(?:\s+[A-Za-zА-Яа-яЁё/,-]{2,40})?"
    r"(?:\s*\(\s*Incoterms?\s*[^)]{2,24}\))?",
    re.I,
)
_INCOTERM_CODE = re.compile(
    r"^(EX-WORKS?|EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP|DAT|DDU)\b",
    re.I,
)
_COMPANY = re.compile(
    r"("
    r"(?:LLC|ООО)\s+[A-ZА-ЯЁ0-9\"«][A-ZА-ЯЁ0-9\"«»'’.-]{0,40}"
    r"|"
    r"[A-Z][A-Z0-9 .,&'’/-]{3,80}?(?:COMPANY\s+LIMITED|CO\.,?\s*LTD\.?|LIMITED|GMBH|SANAYI[^\n|]{0,20}|A\.?\s*Ş\.?|A\.S\.|SRL|LLC)\b"
    r")",
    re.I,
)
_SKIP_VALUE = re.compile(
    r"^(buyer|seller|date|invoice|contract|add|address|tel|fax|:|-)?$",
    re.I,
)
_ROLE_ONLY = re.compile(
    r"^(?:the\s+)?(?:buyer|seller|recipient|consignee|shipper|exporter|manufacturer|"
    r"delivery(?:\s+basis)?|address|add|покупатель|продавец|получатель)$",
    re.I,
)
_HEADERISH = re.compile(
    r"\b(?:weight|price|amount|qty|quantity|description|brand|netto|brutto|origin)\b",
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
        r"\s+(?:\d{5,}|OGRN|ОГРН|TIN|INN|ИНН|KPP|КПП|Address|Add:|Russian Federation|Российск|Italy|Italia|Turkey|T[uü]rkiye|China|"
        r"THE\s+DELIVERY|THE\s+BUYER|THE\s+SELLER|RECIPIENT|CONTRACT|SPECIFICATIONS)\b",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    if not text:
        return None
    parts = text.split()
    half = len(parts) // 2
    if half >= 2 and len(parts) == half * 2 and parts[:half] == parts[half:]:
        text = " ".join(parts[:half])
    return text[:160] if text and not _is_role_only(text) else None


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


_GLUED_ADDRESS_LABEL = re.compile(
    r"\b(?:CONTRACT|THE BUYER|THE SELLER|INVOICE|SPECIFICATIONS|BANK:|SWIFT:)\b",
    re.I,
)


def _clean_address(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).replace("\\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip(" :：|/")
    text = re.sub(r"^(?:address\s*:?\s*)+", "", text, flags=re.I)
    text = _GLUED_ADDRESS_LABEL.split(text, maxsplit=1)[0]
    text = text.strip(" :：|/,")
    if len(text) < 6:
        return None
    return text


def split_party_address(text: str | None, *, seller: bool) -> str | None:
    """Pick seller vs buyer chunk when PDF glued both Address: blocks into one line."""
    raw = str(text or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^(?:address\s*:?\s*)+", "", raw, flags=re.I)
    chunks = [c.strip(" :") for c in re.split(r"\baddress\s*:", raw, flags=re.I) if c.strip()]
    if not chunks:
        chunks = [raw]

    def _score(chunk: str) -> int:
        low = chunk.lower()
        ru = 1 if re.search(r"\b(?:inn|kpp|moscow|моск|подольск|podolsk)\b", low) else 0
        overseas = 1 if re.search(r"\b(?:hong\s*kong|hongkong|china|trend ctr)\b", low) else 0
        bank = 1 if re.search(r"\b(?:bank|swift|account)\b", low) else 0
        if seller:
            return overseas * 3 - ru * 2 - bank
        return ru * 3 - overseas * 2 - bank

    chunks.sort(key=lambda chunk: (_score(chunk), len(chunk)), reverse=True)
    chosen = chunks[0]
    chosen = re.split(r"\b(?:bank|swift|inn\b|kpp\b)\b", chosen, maxsplit=1, flags=re.I)[0]
    chosen = chosen.strip(" ,;")
    if "\n" in chosen:
        first, rest = chosen.split("\n", 1)
        first = first.strip()
        rest = rest.strip()
        leftover = bool(rest) and len(rest) <= 24 and not re.search(
            r"\b(?:ogrn|огрн|tin|inn|инн|kpp|кпп|\d{5,6}|street|road|region|st\.|bldg|apt)\b",
            rest,
            re.I,
        )
        if leftover and first and _ADDRESS_HINT.search(first) and len(first) >= 12:
            chosen = first
    return chosen[:240] or None


def _address_followup(blob: str, end: int) -> str | None:
    chunk = blob[end : end + 500]
    lines: list[str] = []
    for line in chunk.splitlines():
        line = line.strip(" |")
        if not line:
            if lines:
                break
            continue
        if re.match(r"^address\s*:", line, re.I):
            cut = _GLUED_ADDRESS_LABEL.split(line, maxsplit=1)[0].strip(" :，,")
            if cut:
                lines.append(cut)
            if len(lines) >= 3:
                break
            continue
        if re.match(
            r"^(buyer|seller|inv\.?|invoice|contract|date|packing|ex-work|"
            r"specification|recipient|the\s+buyer|the\s+seller|terms of|"
            r"bank|inn\b|swift|account|kpp\b|bic\b)\b",
            line,
            re.I,
        ):
            break
        if _is_role_only(line):
            continue
        if _COMPANY.search(line):
            continue
        cut = _GLUED_ADDRESS_LABEL.split(line, maxsplit=1)[0].strip(" :，,")
        if _ADDRESS_HINT.search(cut or line):
            if cut:
                lines.append(cut)
            if len(lines) >= 3:
                break
            continue
        if lines:
            break
    return "\n".join(lines).strip() or None


def _clean_invoice_no(raw: str | None) -> str | None:
    text = _clean_value(raw)
    if not text:
        return None
    match = re.search(r"([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9._/-]{2,})", text, re.I)
    if not match:
        return None
    value = _fold_lookalikes(match.group(1)).strip(" .")
    if value.upper() in {"NO", "NR", "DATE", "INV"}:
        return None
    # A real invoice id has a digit. Nearby titles ("COMMERCIAL") are not numbers.
    if not re.search(r"\d", value):
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
    if not re.search(r"\d", text):
        return None
    if re.fullmatch(r"(?:in|of|the|and|to|for|this|case|no|nr)", text, re.I):
        return None
    return _fold_lookalikes(text)[:80]


def _is_role_only(text: str | None) -> bool:
    cleaned = re.sub(r"[:：].*$", "", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return bool(cleaned) and bool(_ROLE_ONLY.match(cleaned))


def _companies_on_line(line: str) -> list[str]:
    found: list[str] = []
    for match in _COMPANY.finditer(line or ""):
        name = _clean_party(match.group(1))
        if name and name not in found:
            found.append(name)
    return found


def _party_from_match(blob: str, match: re.Match[str], *, role: str) -> tuple[str | None, str | None]:
    name, addr = _split_party_block(match.group(1))
    if name and not _is_role_only(name):
        return _prefer_party_name(name, match.group(1), role), addr
    rest = blob[match.end() :]
    for line in rest.splitlines():
        line = line.strip(" |")
        if not line:
            continue
        name, addr = _split_party_block(line)
        picked = _prefer_party_name(name, line, role)
        if picked and not _is_role_only(picked):
            return picked, addr
        break
    return None, None


def _prefer_party_name(name: str | None, line: str, role: str) -> str | None:
    companies = _companies_on_line(line)
    if role == "buyer":
        llcs = [item for item in companies if re.search(r"\b(?:LLC|ООО)\b", item, re.I)]
        if llcs:
            return llcs[0]
    if role == "seller":
        corps = [
            item
            for item in companies
            if re.search(r"\b(?:LIMITED|LTD|CO\.|GMBH|SANAYI)\b", item, re.I)
            and not re.search(r"\b(?:LLC|ООО)\b", item, re.I)
        ]
        if corps:
            return corps[0]
    return name


def _hits_from_text(blob: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = defaultdict(list)

    def add(key: str, value: str | None) -> None:
        if key == "seller_address":
            cleaned = split_party_address(value, seller=True) or _clean_address(value)
        elif key == "buyer_address":
            cleaned = split_party_address(value, seller=False) or _clean_address(value)
        elif key in {"buyer_address", "seller_address", "warehouse_address"}:
            cleaned = _clean_address(value)
        else:
            cleaned = _clean_value(value)
        if cleaned:
            hits[key].append(cleaned)

    for match in _INVOICE_NO.finditer(blob):
        add("invoice_no", _clean_invoice_no(match.group(1)))
    for match in _INVOICE_DATE_NUMER.finditer(blob):
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
        name, addr = _party_from_match(blob, match, role="buyer")
        add("buyer", name)
        add("buyer_address", addr or _address_followup(blob, match.end()))
    for match in _SELLER.finditer(blob):
        name, addr = _party_from_match(blob, match, role="seller")
        add("seller", name)
        add("seller_address", addr or _address_followup(blob, match.end()))
    for match in _MANUFACTURER.finditer(blob):
        mfr = _clean_party(match.group(1))
        if mfr and _HEADERISH.search(mfr) and not re.search(r"\b(?:ltd|llc|corp|co\.)\b", mfr, re.I):
            continue
        add("manufacturer", mfr)
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
        elif key in {"invoice_no", "spec_no"}:
            cleaned = _clean_invoice_no(value)
        elif key == "contract_no":
            cleaned = _clean_contract_no(value)
        elif key == "seller_address":
            cleaned = split_party_address(value, seller=True) or _clean_address(value)
        elif key == "buyer_address":
            cleaned = split_party_address(value, seller=False) or _clean_address(value)
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
        if key == "delivery_terms":
            picked = _pick_incoterm(by_field.get(key) or [])
        elif key in {"buyer_address", "seller_address", "warehouse_address"}:
            picked = _pick_address(by_field.get(key) or [])
        else:
            picked = _pick_agreed(by_field.get(key) or [])
        if picked:
            result[key] = picked
    if "invoice_no" not in result:
        spec_no = _pick_agreed(by_field.get("spec_no") or [])
        if spec_no:
            result["invoice_no"] = spec_no
    if "seller" not in result and letterheads:
        agreed_seller = _pick_agreed([(f"letterhead:{i}", name) for i, name in enumerate(letterheads)])
        if agreed_seller:
            result["seller"] = agreed_seller
        elif len({normalize_text(n).lower() for n in letterheads}) == 1:
            result["seller"] = letterheads[0]
    # Seller is the trading party; manufacturer is the goods maker/brand.
    # Never copy seller into manufacturer — fill from Manufacturer column or goods rows.
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


def _compact_address(value: str) -> str:
    return re.sub(r"[\s,.;:\"'“”«»-]+", "", normalize_text(value)).lower()


def _pick_address(pairs: list[tuple[str, str]]) -> str | None:
    values = [value for _source, value in pairs if value]
    if not values:
        return None
    ranked = sorted(values, key=lambda value: len(_compact_address(value)), reverse=True)
    top = _compact_address(ranked[0])
    if top and all(
        top.startswith(_compact_address(value)) or _compact_address(value).startswith(top)
        for value in ranked
    ):
        return ranked[0]
    return _pick_agreed(pairs)


def _latin_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isascii() for c in letters) / len(letters)


def _pick_incoterm(pairs: list[tuple[str, str]]) -> str | None:
    if not pairs:
        return None
    buckets: dict[str, list[str]] = defaultdict(list)
    leftover: list[str] = []
    for _source, value in pairs:
        if not value:
            continue
        match = _INCOTERM_CODE.match(value.strip())
        if match:
            buckets[match.group(1).upper()].append(value)
        else:
            leftover.append(value)
    if buckets:
        code = max(buckets, key=lambda key: (len(buckets[key]), max(_latin_ratio(v) for v in buckets[key])))
        return max(buckets[code], key=_latin_ratio)
    if leftover:
        return _pick_agreed([("", value) for value in leftover])
    return None


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


def _item_field(item: Any, *keys: str) -> Any:
    if item is None:
        return None
    if isinstance(item, dict):
        for key in keys:
            if item.get(key) not in (None, ""):
                return item.get(key)
        commercial = item.get("commercial_data") or {}
        customs = item.get("customs_data") or {}
        fields = item.get("fields") or {}
        for bag in (commercial, customs, fields):
            for key in keys:
                if bag.get(key) not in (None, ""):
                    return bag.get(key)
        return None
    fields = getattr(item, "fields", None) or {}
    for key in keys:
        if fields.get(key) not in (None, ""):
            return fields.get(key)
    return None


def _majority_text(values: list[str]) -> str | None:
    buckets: dict[str, list[str]] = defaultdict(list)
    for value in values:
        text = normalize_text(value)
        if text:
            buckets[_norm_key(text)].append(text)
    if not buckets:
        return None
    ranked = sorted(buckets.values(), key=len, reverse=True)
    if len(ranked[0]) < max(1, (len(values) + 1) // 2):
        return None
    return ranked[0][0]


def normalize_currency_code(value: Any) -> str | None:
    """ISO from a token or short phrase. Two different codes in one blob → None."""
    return normalize_currency_iso(value)


def currency_from_sources(
    items: list[Any] | None = None,
    header: dict[str, Any] | None = None,
) -> str | None:
    """Invoice currency from goods / header.currency — not from payment-prose lists."""
    codes: list[str] = []
    for item in items or []:
        code = normalize_currency_code(_item_field(item, "currency"))
        if code:
            codes.append(code)
    if codes:
        return _majority_text(codes) or codes[0]
    header = header or {}
    direct = normalize_currency_code(header.get("currency"))
    if direct:
        return direct
    # Payment clauses often list several allowed currencies ("yuan, US dollars").
    # Those are not the invoice price currency — ignore mixed blobs.
    blob = " ".join(str(v) for v in header.values() if v not in (None, ""))
    return normalize_currency_iso(blob)


def export_currency_label(code: str | None, *, hangzhou_style: bool = False) -> str:
    """Label for price/amount column headers.

    Hangzhou templates historically say RMB for CNY; other profiles keep ISO codes.
    """
    normalized = normalize_currency_code(code) or ("CNY" if hangzhou_style else None)
    if not normalized:
        return "USD"
    if hangzhou_style and normalized in {"CNY", "RMB"}:
        return "RMB"
    if normalized == "RMB":
        return "CNY"
    return normalized


def enrich_header_from_goods(
    header: dict[str, Any] | None,
    items: list[Any] | None,
) -> dict[str, Any]:
    """Fill gaps from goods after parsing. Does not belong on export of operator edits.

    - manufacturer: only when empty, or when it was a silent copy of seller
    - currency: from goods / price columns when missing
    """
    out = {k: v for k, v in (header or {}).items() if v not in (None, "", [])}
    mfrs = [
        str(value).strip()
        for value in (_item_field(item, "manufacturer") for item in (items or []))
        if value not in (None, "")
    ]
    goods_mfr = _majority_text(mfrs)
    seller = str(out.get("seller") or "").strip()
    current_mfr = str(out.get("manufacturer") or "").strip()
    if goods_mfr:
        if not current_mfr:
            out["manufacturer"] = goods_mfr
        elif seller and _norm_key(current_mfr) == _norm_key(seller) and _norm_key(goods_mfr) != _norm_key(seller):
            out["manufacturer"] = goods_mfr
    elif current_mfr and seller and _norm_key(current_mfr) == _norm_key(seller):
        # Explicit seller-copy is worse than empty — operator / model should fill maker.
        out.pop("manufacturer", None)

    if not out.get("currency"):
        ccy = currency_from_sources(items, out)
        if ccy:
            out["currency"] = ccy
    return out


def export_header_fields(
    header: dict[str, Any] | None,
    items: list[Any] | None = None,
) -> dict[str, Any]:
    """Header for Excel export: operator values win; fill only empty gaps from goods."""
    out = {k: v for k, v in (header or {}).items() if v not in (None, "")}
    # Fill manufacturer/currency only when the operator left them blank
    # (or left a stale seller-copy that was never corrected).
    if not out.get("manufacturer") or (
        out.get("seller")
        and _norm_key(str(out.get("manufacturer"))) == _norm_key(str(out.get("seller")))
    ):
        goods_mfr = _majority_text(
            [
                str(value).strip()
                for value in (_item_field(item, "manufacturer") for item in (items or []))
                if value not in (None, "")
            ]
        )
        if goods_mfr and (
            not out.get("manufacturer")
            or (
                out.get("seller")
                and _norm_key(str(out.get("manufacturer"))) == _norm_key(str(out.get("seller")))
                and _norm_key(goods_mfr) != _norm_key(str(out.get("seller")))
            )
        ):
            out["manufacturer"] = goods_mfr
    if not out.get("currency"):
        ccy = currency_from_sources(items, out)
        if ccy:
            out["currency"] = ccy
    return out


def manufacturer_from_sources(
    items: list[Any] | None = None,
    header: dict[str, Any] | None = None,
) -> str | None:
    """Goods maker/brand — never silently reuse seller."""
    enriched = export_header_fields(header or {}, items or [])
    value = enriched.get("manufacturer")
    return str(value).strip() if value not in (None, "") else None
