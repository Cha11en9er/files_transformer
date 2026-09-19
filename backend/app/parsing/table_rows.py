"""Turn any table (Excel / PDF / markdown) into product lines."""

from __future__ import annotations

import re
from typing import Any

from app.parsing.normalize import normalize_article, normalize_text
from app.parsing.product_row import is_junk_text, is_plausible_article, numbers_look_like_product
from app.services.field_map import classify_header, is_numeric_token, map_row, parse_number

NUMERIC_KEYS = (
    "qty",
    "price",
    "amount",
    "rolls",
    "boxes",
    "meters",
    "area",
    "width",
    "net_weight",
    "gross_weight",
)

HEADER_HINTS = (
    "артикул",
    "articul",
    "article",
    "sku",
    "item",
    "design",
    "дизайн",
    "product",
    "qty",
    "quantity",
    "price",
    "цена",
    "amount",
    "сумма",
    "weight",
    "вес",
    "нетто",
    "брутто",
    "hs",
    "наименование",
    "модель",
    "model",
    "code",
    "код",
    "meter",
    "рулон",
    "width",
    "ширина",
    "miktar",
    "fiyat",
    "tutar",
    "adet",
    "stok",
    "malzeme",
    "metre",
    "kilogram",
    "sipariş",
    "siparis",
    "müşteri",
    "musteri",
    "ürün",
    "urun",
    "货号",
    "数量",
    "单价",
    "金额",
    "desing",
    "desen",
    "renk",
)

_DATE_RE = re.compile(r"^\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?$")
_CURRENCY = r"(?:USD|EUR|US\$|\$|TL|TRY|USO|US0)"
_QTY_PRICE_LINE = re.compile(
    r"(?im)(?P<article>[A-Z][A-Za-z0-9._/-]{2,24}(?:[ -]\d{2,5})?)\s+"
    r"(?P<qty>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+[.,]\d+|\d+)\s+"
    r"(?:MT\.?|MTS|PCS|KG|M\.)?\s*"
    r"(?P<price>\d+[.,]\d+)\s*" + _CURRENCY
)
_METERS_LINE = re.compile(
    r"(?im)(?P<qty>\d+[.,]\d+|\d+)\s+METERS\s+(?P<article>[A-Z][A-Z0-9 .()/-]{2,70}?)"
    r"(?:\s+\d{6,12})?\s+(?P<price>\d+[.,]\d+)"
)
_MILL_QTY_PRICE = re.compile(
    r"(?im)(?P<article>\d{4}(?:[\s-]+\d{2,6}){2,6})[^\n]{0,160}?"
    r"(?P<qty>\d+[.,]\d+)\s+(?:MT\.?|MTS|METERS?)\s+"
    r"(?P<price>\d+[.,]\d+)"
)
_GLUED_MT_USD = re.compile(
    r"(?i)(?P<article>[A-Z]{3,24})"
    r"(?P<qty>\d{1,3}(?:\.\d{3})+,\d+|\d+,\d+)\s*MT\.?"
    r"(?P<price>\d+[.,]\d+)\s*USD"
    r"[^\n]{0,90}?"
    r"(?P<amount>\d{1,3}(?:\.\d{3})+,\d+|\d+[.,]\d+)\s*USD"
)
_GLUED_CCY_PREFIX = re.compile(r"^(?:USD|EUR|TRY|TL|US)+", re.I)
_WEAVERS_LINE = re.compile(
    r"(?i)(?P<design_no>[A-Z]\d{2}-\d{4})\s*/\s*(?P<article>[A-Z][A-Z0-9 ]{2,40}?)\s*/\s*"
    r"(?P<code>PX[A-Z0-9.]+(?:\s+\d+)?)"
    r"[^\n]{0,160}?"
    r"HS\s*CODE\s*:?\s*\d{8,12}"
    r"(?P<qty>\d+[.,]\d+)\s*MT\.?"
    r"(?P<price>\d+[.,]\d+)\s*\$"
    r"(?P<amount>\d+[.,]\d+)\s*\$"
)
_SKIP_PLAINTEXT = (
    "invoice no",
    "page ",
    "tel:",
    "fax:",
    "e-mail",
    "email",
    "swift",
    "iban",
    "vat no",
    "продавец",
    "покупатель",
    "consignee",
    "shipper",
    "bank name",
    "post code",
    "zip code",
)
_URUN_KODU = re.compile(r"(?i)^(?:ürün|urun)\s*kodu\s*:?\s*(.+)$")
_MUSTERI_KODU = re.compile(r"(?i)^(?:müşteri|musteri)\s*kodu\s*:?\s*(.+)$")
_ARTIKEL_TOP = re.compile(r"(?i)artikel\s*(\d+)\s*top\b")
_PACK_TAIL = re.compile(
    r"(?P<net_m>\d+[.,]\d+)\s+(?P<brut_m>\d+[.,]\d+)\s+"
    r"(?P<brut_kg>\d+[.,]\d+)\s+(?P<net_kg>\d+[.,]\d+)(?:\s+(?P<adet>[-+]?\d+(?:[.,]\d+)?))?\s*$"
)
_GENEL_TOPLAM = re.compile(r"(?i)genel\s*toplam")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return normalize_text(str(value))


def header_score(cells: list[Any]) -> int:
    blob = " ".join(_cell(c).lower() for c in cells)
    if not blob.strip():
        return 0
    hits = sum(1 for hint in HEADER_HINTS if hint in blob)
    if "price" in blob or "цена" in blob or "fiyat" in blob or "单价" in blob:
        hits += 2
    if "артикул" in blob or "article" in blob or "sku" in blob or "货号" in blob:
        hits += 2
    return hits


def best_header_index(rows: list[list[Any]], *, scan: int = 45) -> int | None:
    best_i = None
    best = 0
    for idx, row in enumerate(rows[:scan]):
        score = header_score(row)
        if score > best:
            best = score
            best_i = idx
    return best_i if best >= 1 else None


def has_numeric_fields(mapped: dict[str, Any]) -> bool:
    return any(isinstance(mapped.get(key), (int, float)) for key in NUMERIC_KEYS)


def _close_amount(left: float, right: float) -> bool:
    return abs(left - right) <= max(0.05, 0.08 * max(abs(right), 1e-6))


def assign_numbers(mapped: dict[str, Any], nums: list[float]) -> None:
    nums = [n for n in nums if n is not None]
    if not nums:
        return
    qty = price = amount = meters = None
    if len(nums) >= 4:
        q, meters_or_price, p, total = nums[0], nums[1], nums[2], nums[3]
        if _close_amount(meters_or_price * p, total):
            qty, meters, price, amount = q, meters_or_price, p, total
        elif _close_amount(q * p, total):
            qty, price, amount = q, p, total
        else:
            qty, price, amount = q, meters_or_price, p
    elif len(nums) == 3:
        qty, price, amount = nums[0], nums[1], nums[2]
    elif len(nums) == 2:
        qty, price = nums[0], nums[1]
    else:
        qty = nums[0]

    if mapped.get("qty") is None and qty is not None:
        mapped["qty"] = qty
    if mapped.get("price") is None and price is not None:
        mapped["price"] = price
    if mapped.get("amount") is None and amount is not None:
        mapped["amount"] = amount
    if mapped.get("meters") is None and meters is not None:
        mapped["meters"] = meters


def enrich_mapped(raw: dict[str, Any], mapped: dict[str, Any]) -> dict[str, Any]:
    """Fill article / qty / price when column titles are missing or unknown."""
    mapped = dict(mapped)
    texts: list[str] = []
    nums: list[float] = []
    for key, value in raw.items():
        header = _cell(key).lower()
        if header and classify_header(header):
            continue
        text = _cell(value)
        if not text:
            continue
        if is_numeric_token(value) and not _DATE_RE.match(text.replace(" ", "")):
            num = parse_number(value)
            if num is not None:
                nums.append(num)
            continue
        if not is_junk_text(text) and not text.endswith(":"):
            texts.append(text)

    if not mapped.get("article") and texts:
        ranked = sorted(texts, key=lambda item: (len(item), item))
        mapped["article"] = next(
            (item for item in ranked if any(ch.isdigit() for ch in item) and any(ch.isalpha() for ch in item)),
            ranked[0],
        )

    if not has_numeric_fields(mapped):
        assign_numbers(mapped, nums)
    return mapped


def pick_article(raw: dict[str, Any], mapped: dict[str, Any]) -> str:
    for key in ("article", "model"):
        text = _cell(mapped.get(key))
        if text and not is_junk_text(text):
            return text
    ranked: list[tuple[int, str]] = []
    for col, value in raw.items():
        text = _cell(value)
        if not text or is_junk_text(text):
            continue
        if text.replace(" ", "").replace(".", "").replace(",", "").isdigit():
            continue
        lowered = str(col).lower()
        rank = 50
        if any(
            tok in lowered
            for tok in ("артикул", "articul", "article", "art no", "sku", "item no", "style", "stok", "desing", "desen")
        ):
            rank = 0
        elif "design" in lowered or "дизайн" in lowered or "model" in lowered or "renk" in lowered:
            rank = 1
        elif "product" in lowered or "наименование" in lowered or "desc" in lowered:
            rank = 3
        ranked.append((rank, text))
    if not ranked:
        desc = _cell(mapped.get("description"))
        return desc if desc and not is_junk_text(desc) else ""
    ranked.sort()
    return ranked[0][1]


def is_usable_row(article: str, mapped: dict[str, Any]) -> bool:
    label = (article or "").strip().upper()
    if label.startswith(("TOTAL", "ИТОГО", "СУММА", "GRAND TOTAL", "GENEL")):
        return False
    if article and any(
        tok in article.lower()
        for tok in ("директор", "director", "swift", "iban", "генеральн", "продавец", "покупатель")
    ):
        return False
    if article and not is_plausible_article(article):
        return False
    if not numbers_look_like_product(mapped):
        return False
    if has_numeric_fields(mapped):
        return True
    if article and (mapped.get("tnved_code") or mapped.get("hs_code")):
        return True
    return False


def build_line(
    raw: dict[str, Any],
    *,
    sheet_name: str,
    row_index: int,
) -> dict[str, Any] | None:
    mapped = enrich_mapped(raw, map_row(raw))
    article = pick_article(raw, mapped) or _cell(mapped.get("article"))
    color = mapped.get("color")
    if article and color and re.fullmatch(r"\d{1,4}", str(color).strip()) and not re.search(r"\d", article):
        article = f"{article} {int(color):02d}"
        mapped["article"] = article
    if not is_usable_row(article, mapped):
        return None
    if not article:
        article = f"позиция {row_index}"
    stored = dict(raw)
    for key, value in mapped.items():
        if value is not None and value != "":
            stored[key] = value
    return {
        "row_index": row_index,
        "sheet_name": sheet_name,
        "article": article,
        "model": mapped.get("model"),
        "normalized_article": normalize_article(article),
        "raw": stored,
    }


def unique_columns(headers: list[Any]) -> list[str]:
    seen: dict[str, int] = {}
    columns: list[str] = []
    for raw in headers:
        base = _cell(raw).lower() or "column"
        n = seen.get(base, 0)
        seen[base] = n + 1
        columns.append(base if n == 0 else f"{base}_{n+1}")
    return columns


def _is_total_row(values: list[Any]) -> bool:
    first = _cell(values[0] if values else "").upper().replace(":", "")
    blob = " ".join(_cell(v).upper() for v in values[:3])
    return first in {"TOTAL", "ИТОГО", "ВСЕГО", "GRAND TOTAL"} or blob.startswith("TOTAL")


def _is_detail_section(values: list[Any]) -> bool:
    blob = " ".join(_cell(v).lower() for v in values)
    return "detail packing" in blob or blob.strip() in {"packing list", "invoice"}


def _accumulate_raw(
    target: dict[str, Any],
    incoming: dict[str, Any],
    *,
    skip_fields: set[str] | None = None,
) -> None:
    skip_fields = skip_fields or set()
    mapped_new = map_row(incoming)
    mapped_old = map_row(target)
    for key in ("qty", "rolls", "meters", "area", "net_weight", "gross_weight", "boxes", "volume", "amount"):
        if key in skip_fields:
            continue
        extra = mapped_new.get(key)
        if not isinstance(extra, (int, float)):
            continue
        current = mapped_old.get(key)
        if key in {"qty", "amount", "price"} and current not in (None, "") and extra == current:
            continue
        total = (current or 0) + extra
        mapped_old[key] = total
        target[key] = total
        for column in list(target):
            if classify_header(str(column)) == key:
                target[column] = total


def _inherited_fields(
    abs_idx: int,
    columns: list[str],
    inherited_cells: set[tuple[int, int]],
) -> set[str]:
    fields: set[str] = set()
    for col_i, name in enumerate(columns):
        if (abs_idx, col_i) not in inherited_cells:
            continue
        field = classify_header(str(name))
        if field:
            fields.add(field)
        low = str(name).lower()
        if any(tok in low for tok in ("art no", "артикул", "article", "item")):
            fields.add("article")
    return fields


def _qty_identity(mapped: dict[str, Any]) -> str:
    qty = mapped.get("qty")
    if qty is None:
        qty = mapped.get("meters")
    try:
        if qty in (None, ""):
            return ""
        return f"{round(float(qty), 6):g}"
    except (TypeError, ValueError):
        return str(qty)


def lines_from_matrix(
    rows: list[list[Any]],
    *,
    sheet_name: str,
    header_row: int | None = None,
    inherited_cells: set[tuple[int, int]] | None = None,
    continuation_rows: set[int] | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    if header_row is None:
        header_row = best_header_index(rows)
    if header_row is None:
        columns = [f"column_{i+1}" for i in range(max(len(r) for r in rows))]
        body = rows
        start = 0
    else:
        columns = unique_columns(rows[header_row])
        body = rows[header_row + 1 :]
        start = header_row + 1
        if body and header_score(body[0]) >= 1:
            numeric = sum(1 for cell in body[0] if is_numeric_token(cell))
            if numeric <= 1:
                body = body[1:]
                start += 1

    inherited_cells = inherited_cells or set()
    continuation_rows = continuation_rows or set()
    lines: list[dict[str, Any]] = []
    for offset, values in enumerate(body):
        abs_idx = start + offset
        if not any(_cell(v) for v in values):
            continue
        if _is_total_row(values) or _is_detail_section(values):
            break
        if lines and header_score(values) >= 3:
            break
        raw = {columns[i]: (values[i] if i < len(values) else None) for i in range(len(columns))}
        mapped = map_row(raw)
        inherited = _inherited_fields(abs_idx, columns, inherited_cells)
        own_qty = mapped.get("qty") not in (None, "") and "qty" not in inherited
        has_article_col = False
        article_cell_empty = True
        for col, value in raw.items():
            low = str(col).lower()
            if classify_header(str(col)) == "article" or any(
                tok in low for tok in ("art no", "артикул", "article", "sku", "item no")
            ):
                has_article_col = True
                if _cell(value):
                    article_cell_empty = False
                    break
        article_missing = has_article_col and article_cell_empty
        continue_prev = bool(lines) and (
            abs_idx in continuation_rows
            or "qty" in inherited
            or "article" in inherited
            or article_missing
        )
        if continue_prev and not own_qty:
            skip = {field for field in ("qty", "price", "amount") if field in inherited or article_missing}
            _accumulate_raw(lines[-1]["raw"], raw, skip_fields=skip)
            continue
        if article_missing and lines and own_qty:
            prev_article = lines[-1].get("article")
            mapped["article"] = prev_article
            raw = dict(raw)
            filled = False
            for column in list(raw):
                if classify_header(str(column)) == "article":
                    raw[column] = prev_article
                    filled = True
                    break
            if not filled:
                raw["article"] = prev_article
        if "qty" not in inherited:
            for field in ("net_weight", "gross_weight", "volume", "boxes", "measurement"):
                if field in inherited and field in mapped:
                    mapped.pop(field, None)
                    for column in list(raw):
                        if classify_header(str(column)) == field:
                            raw[column] = None
        line = build_line(raw, sheet_name=sheet_name, row_index=abs_idx)
        if line:
            lines.append(line)
    return lines


def _packing_numbers(line: str) -> dict[str, float] | None:
    match = _PACK_TAIL.search(line or "")
    if not match:
        return None
    net_m = parse_number(match.group("net_m"))
    brut_kg = parse_number(match.group("brut_kg"))
    net_kg = parse_number(match.group("net_kg"))
    if net_m is None or brut_kg is None or net_kg is None:
        return None
    return {
        "meters": net_m,
        "gross_weight": brut_kg,
        "net_weight": net_kg,
    }


def lines_from_labeled_packing(text: str, *, sheet_name: str = "packing_text") -> list[dict[str, Any]]:
    """Turkish/EU packing blocks: Ürün Kodu / Müşteri Kodu then lot rows and Artikel N Top."""
    current_urun = ""
    current_musteri = ""
    lots: list[dict[str, float]] = []
    groups: list[dict[str, Any]] = []

    def flush(rolls: float | None, totals: dict[str, float] | None) -> None:
        nonlocal lots
        article = (current_musteri or current_urun).strip()
        if not article:
            lots = []
            return
        numbers = totals
        if numbers is None and lots:
            numbers = {
                "meters": round(sum(item["meters"] for item in lots), 4),
                "gross_weight": round(sum(item["gross_weight"] for item in lots), 4),
                "net_weight": round(sum(item["net_weight"] for item in lots), 4),
            }
        lots = []
        if not numbers:
            return
        raw = {
            "article": article,
            "model": current_urun or None,
            "musteri kodu": current_musteri or article,
            "ürün kodu": current_urun or None,
            "meters": numbers["meters"],
            "net metre": numbers["meters"],
            "net_weight": numbers["net_weight"],
            "gross_weight": numbers["gross_weight"],
            "rolls": rolls if rolls is not None else None,
        }
        groups.append(raw)

    for raw_line in (text or "").splitlines():
        line = normalize_text(raw_line)
        if not line:
            continue
        if _GENEL_TOPLAM.search(line):
            flush(None, None)
            break
        urun = _URUN_KODU.match(line)
        if urun:
            flush(None, None)
            current_urun = urun.group(1).strip()
            continue
        musteri = _MUSTERI_KODU.match(line)
        if musteri:
            current_musteri = musteri.group(1).strip()
            continue
        artikel = _ARTIKEL_TOP.search(line)
        if artikel:
            rolls = parse_number(artikel.group(1))
            flush(rolls, _packing_numbers(line))
            continue
        numbers = _packing_numbers(line)
        if numbers and not line.lower().startswith("lot "):
            lots.append(numbers)

    flush(None, None)
    lines: list[dict[str, Any]] = []
    for idx, raw in enumerate(groups, start=1):
        built = build_line(raw, sheet_name=sheet_name, row_index=idx)
        if built:
            lines.append(built)
    return lines


def lines_from_plaintext(text: str, *, sheet_name: str = "text") -> list[dict[str, Any]]:
    """Last-resort parser for PDF text when table detection failed."""
    lines: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(text.splitlines()):
        line = normalize_text(raw_line)
        low = line.lower()
        if len(line) < 5:
            continue
        if any(tok in low for tok in _SKIP_PLAINTEXT):
            continue
        if low.startswith(("total", "итого", "genel toplam", "grand total")):
            continue
        tokens = line.split()
        nums: list[float] = []
        words: list[str] = []
        for tok in tokens:
            if _DATE_RE.match(tok):
                continue
            if is_numeric_token(tok):
                num = parse_number(tok)
                if (
                    num is not None
                    and words
                    and float(num).is_integer()
                    and 10 <= abs(num) <= 9999
                    and len(nums) == 0
                    and any(ch.isalpha() for ch in words[-1])
                ):
                    words.append(tok)
                    continue
                if num is not None:
                    nums.append(num)
                continue
            words.append(tok)
        if len(nums) < 2 or not words:
            continue
        article = " ".join(words)
        blob = article.lower()
        if sum(1 for hint in HEADER_HINTS if hint in blob) >= 2:
            continue
        if is_junk_text(article) or len(article) > 80:
            continue
        if not any(ch.isalpha() for ch in article):
            continue
        if not any(ch.isdigit() for ch in article) and len(nums) < 3:
            continue
        raw: dict[str, Any] = {"article": article}
        assign_numbers(raw, nums)
        built = build_line(raw, sheet_name=sheet_name, row_index=idx + 1)
        if built:
            lines.append(built)
    return lines


def _clean_glued_article(blob: str) -> str:
    text = _GLUED_CCY_PREFIX.sub("", blob or "")
    if len(text) > 8:
        return text[-5:]
    return text


def lines_from_qty_price_text(text: str, *, sheet_name: str = "qty_price") -> list[dict[str, Any]]:
    """Invoice-style lines: ARTICLE 1.740,82 MT. 5,78 USD or 110.60 METERS BLOOM … 5.40."""
    lines: list[dict[str, Any]] = []
    seen: set[str] = set()
    matches = (
        list(_MILL_QTY_PRICE.finditer(text or ""))
        + list(_WEAVERS_LINE.finditer(text or ""))
        + list(_GLUED_MT_USD.finditer(text or ""))
        + list(_QTY_PRICE_LINE.finditer(text or ""))
        + list(_METERS_LINE.finditer(text or ""))
    )
    for idx, match in enumerate(matches, start=1):
        article = normalize_text(match.group("article"))
        if match.re is _GLUED_MT_USD:
            article = _clean_glued_article(article)
        qty = parse_number(match.group("qty"))
        price = parse_number(match.group("price"))
        if not article or qty is None or price is None:
            continue
        key = normalize_article(article)
        if key in seen:
            continue
        seen.add(key)
        amount = None
        if "amount" in match.re.groupindex:
            amount = parse_number(match.group("amount"))
        raw = {
            "article": article,
            "qty": qty,
            "price": price,
            "amount": amount if amount is not None else round(qty * price, 2),
        }
        built = build_line(raw, sheet_name=sheet_name, row_index=idx)
        if built:
            lines.append(built)
    return _prefer_mill_articles(lines)


def _is_mill_article(article: str | None) -> bool:
    compact = str(article or "").replace(" ", "").replace("-", "")
    return compact.isdigit() and len(compact) >= 8


def _qty_price_key(line: dict[str, Any]) -> tuple[float, float] | None:
    raw = line.get("raw") or {}
    qty = raw.get("qty")
    price = raw.get("price")
    if not isinstance(qty, (int, float)) or not isinstance(price, (int, float)):
        return None
    return round(float(qty), 4), round(float(price), 4)


def _prefer_mill_articles(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mill_qp = {_qty_price_key(line) for line in lines if _is_mill_article(line.get("article"))}
    mill_qp.discard(None)
    if not mill_qp:
        return lines
    return [
        line
        for line in lines
        if _is_mill_article(line.get("article")) or _qty_price_key(line) not in mill_qp
    ]


def merge_line_groups(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the richest row per article+qty lot; drop empty duplicates and OCR fragments."""
    by_key: dict[str, tuple[int, dict[str, Any]]] = {}
    extras: list[dict[str, Any]] = []
    for group in groups:
        for line in group or []:
            mapped = map_row(line.get("raw") or {})
            score = sum(1 for key in NUMERIC_KEYS if isinstance(mapped.get(key), (int, float)))
            if line.get("article"):
                score += 1
            article_key = line.get("normalized_article") or ""
            if not article_key:
                extras.append(line)
                continue
            qty_part = _qty_identity(mapped)
            key = f"{article_key}#{qty_part}" if qty_part else article_key
            prev = by_key.get(key)
            if prev is None:
                by_key[key] = (score, line)
                continue
            if score > prev[0]:
                _fill_empty_raw(line.get("raw") or {}, prev[1].get("raw") or {})
                by_key[key] = (score, line)
            else:
                _fill_empty_raw(prev[1].get("raw") or {}, line.get("raw") or {})
    known = list(by_key)
    kept_extras: list[dict[str, Any]] = []
    for line in extras:
        article = normalize_article(line.get("article"))
        if article and any(key and key != article and (article.startswith(key) or key in article) for key in known):
            continue
        kept_extras.append(line)
    by_key = _drop_component_skus(by_key)
    return _prefer_mill_articles([item[1] for item in by_key.values()] + kept_extras)


def _article_key_part(key: str) -> str:
    return key.split("#", 1)[0]


def _drop_component_skus(by_key: dict[str, tuple[int, dict[str, Any]]]) -> dict[str, tuple[int, dict[str, Any]]]:
    keys = list(by_key)
    articles = {key: _article_key_part(key) for key in keys}
    drop: set[str] = set()
    for key in keys:
        mapped = map_row(by_key[key][1].get("raw") or {})
        if mapped.get("price") not in (None, ""):
            continue
        article = articles[key]
        if any(
            parent != article and article.startswith(parent) and len(article) > len(parent)
            for parent in articles.values()
        ):
            drop.add(key)
    return {key: value for key, value in by_key.items() if key not in drop}


def _fill_empty_raw(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    mapped_target = map_row(target)
    mapped_in = map_row(incoming)
    for key, value in mapped_in.items():
        if value in (None, "") or mapped_target.get(key) not in (None, ""):
            continue
        target[key] = value
        mapped_target[key] = value
