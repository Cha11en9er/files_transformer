"""Sheet -> canonical rows.

Given one `Sheet` (grid with merges expanded) we:
1. find the header row (the row whose columns map to the most canonical fields);
2. classify the sheet role (goods / packing / specification / catalog / mixed);
3. read every data row into canonical fields, handling
   - blank-article continuation rows (a lot of the same article split over rows),
   - the "numbered child + unnumbered SOFA FABRIC family" invoice layout where the
     family row carries the unit price for its children.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.parsing.header_extract import is_catalog_filename
from app.parsing.normalize import normalize_article, normalize_text
from app.transform.canonical import (
    NUMERIC_FIELDS,
    ColumnStat,
    classify_columns,
    is_number_like,
    parse_number,
)
from app.transform.reader import Sheet

HEADER_SCAN = 25
SAMPLE = 20

_TOTAL_RE = re.compile(r"^\s*(total|grand\s*total|итого|всего|genel\s*toplam|合计|总计)\b", re.IGNORECASE)
_DETAIL_SECTION_RE = re.compile(r"detail\s*packing|подробн", re.IGNORECASE)
_NAKED_SECTION_RE = re.compile(r"^\s*(packing\s*list|invoice|specification|инвойс|упаковочн|спецификац)\s*$", re.IGNORECASE)
_JUNK_RE = re.compile(
    r"(terms of (?:delivery|payment)|bank name|swift|director|sold to|consignee|"
    r"beneficiary|account\s*no|tel\s*:|fax\s*:|e-?mail|инн\b|огрн|б\/сч)",
    re.IGNORECASE,
)
_PACKING_FIELDS = frozenset(
    {"net_weight", "gross_weight", "volume", "boxes", "rolls", "measurement", "pcs_per_carton"}
)
_COMMERCIAL_FIELDS = frozenset({"qty", "price", "amount", "unit", "meters", "currency"})

ROLE_KEYWORDS = {
    "invoice": ("commercial invoice", "invoice", "инвойс", "商业发票", "发票", "фактура"),
    "packing": ("packing list", "упаковочный", "装箱单", "çeki", "ceki", "seçme listesi", "packing"),
    "specification": ("specification", "спецификация", "规格"),
    "catalog": ("справочник", "сводная", "catalog", "описание", "риск"),
}


_LINE_FIELDS = (
    "qty",
    "unit",
    "price",
    "amount",
    "color",
    "net_weight",
    "gross_weight",
    "volume",
    "boxes",
    "rolls",
    "measurement",
    "pcs_per_carton",
    "meters",
    "area",
    "width",
)
_SKU_NOTE_RE = re.compile(r"^[A-Za-z0-9]{1,12}(?:[._/-][A-Za-z0-9]{1,12}){1,4}$")
_COMPONENT_HEADER_RE = re.compile(r"pallet|qty\s*/\s*ctn|qty/ctn", re.IGNORECASE)


@dataclass
class Row:
    article: str
    normalized: str
    fields: dict[str, Any]
    source: str
    role: str
    item_no: int | None = None
    is_group: bool = False
    component: bool = False
    lines: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExtractedSheet:
    name: str
    source: str
    role: str
    mapping: dict[int, str]          # col index -> canonical field
    header_text: str
    rows: list[Row] = field(default_factory=list)
    component_rows: list[Row] = field(default_factory=list)  # post-TOTAL detail packing, not goods
    detail: bool = False             # per-roll detail sheet (aggregate by sum on merge)
    letterhead: list[list[str]] = field(default_factory=list)  # cells above the table header


def _cell(v: Any) -> str:
    return normalize_text("" if v is None else str(v))


def _letterhead_cell(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("\r\n", "\n").replace("\r", "\n").strip()


def _score_mapping(mapping: dict[int, str]) -> int:
    fields = set(mapping.values())
    score = 0
    if "article" in fields or "description" in fields:
        score += 2
    score += len(fields & {"qty", "meters", "area", "rolls"})
    score += len(fields & {"price", "amount"})
    score += len(fields & {"net_weight", "gross_weight"})
    score += len(fields & {"hs_code", "customs_code"})
    return score


def _build_columns(sheet: Sheet, header_row: int) -> list[ColumnStat]:
    headers = sheet.grid[header_row]
    cols: list[ColumnStat] = []
    for c in range(sheet.ncols):
        header = _cell(headers[c] if c < len(headers) else "")
        values = [
            sheet.grid[r][c]
            for r in range(header_row + 1, min(header_row + 1 + SAMPLE, sheet.nrows))
            if c < len(sheet.grid[r])
        ]
        cols.append(ColumnStat(index=c, header=header, values=values))
    return cols


def find_header(sheet: Sheet) -> tuple[int, dict[int, str], list[ColumnStat]]:
    """Return (header_row_index, mapping, columns). header_row = -1 if none good."""
    best: tuple[int, int, dict[int, str], list[ColumnStat]] | None = None
    limit = min(sheet.nrows, HEADER_SCAN)
    for r in range(limit):
        cols = _build_columns(sheet, r)
        mapping = classify_columns(cols)
        score = _score_mapping(mapping)
        if best is None or score > best[0]:
            best = (score, r, mapping, cols)
    if best is None or best[0] < 3:
        return -1, {}, []
    return best[1], best[2], best[3]


def _sheet_text(sheet: Sheet, header_row: int) -> str:
    top = " ".join(
        _cell(v)
        for r in range(0, max(header_row, 0) + 1)
        for v in sheet.grid[r]
    )
    return f"{sheet.name} {top}".lower()


def classify_role(text: str, mapping_fields: set[str], source: str = "") -> str:
    if is_catalog_filename(source):
        return "catalog"
    scores = {role: 0 for role in ROLE_KEYWORDS}
    for role, words in ROLE_KEYWORDS.items():
        for w in words:
            if w in text:
                scores[role] += text.count(w)
    # structural hints
    has_price = bool(mapping_fields & {"price", "amount"})
    has_weight = bool(mapping_fields & {"net_weight", "gross_weight", "measurement", "volume"})
    has_code = bool(mapping_fields & {"hs_code", "customs_code"})
    has_shipping_qty = bool(mapping_fields & {"qty", "meters", "rolls", "area"})

    if scores["catalog"] and has_code and not has_shipping_qty:
        return "catalog"
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_role, top_score = ranked[0]
    if top_score >= 1 and (ranked[1][1] == 0 or top_score - ranked[1][1] >= 1):
        # keyword winner, but sanity-check against structure for mixed sheets
        if top_role == "packing" and has_price and "invoice" in text:
            return "mixed"
        return top_role
    # fall back to structure
    if has_price and has_weight:
        return "mixed"
    if has_price:
        return "invoice"
    if has_weight:
        return "packing"
    if has_code and not has_shipping_qty:
        return "catalog"
    return "goods"


def _row_fields(sheet: Sheet, r: int, mapping: dict[int, str]) -> tuple[dict[str, Any], set[str]]:
    out: dict[str, Any] = {}
    inherited: set[str] = set()
    row = sheet.grid[r]
    for c, field_key in mapping.items():
        if c >= len(row):
            continue
        raw = row[c]
        text = _cell(raw)
        if not text:
            continue
        if (r, c) in sheet.inherited:
            inherited.add(field_key)
        if field_key in NUMERIC_FIELDS:
            num = parse_number(raw)
            if num is not None:
                out[field_key] = num
        elif field_key == "measurement":
            out[field_key] = text
        else:
            out[field_key] = text
    return out, inherited


def _is_stop_row(joined: str, first_cells: str) -> bool:
    if _TOTAL_RE.match(first_cells) or _TOTAL_RE.match(joined):
        return True
    if _DETAIL_SECTION_RE.search(joined):
        return True
    if _NAKED_SECTION_RE.match(joined):
        return True
    return False


def _is_letterhead_junk(joined: str) -> bool:
    return bool(_JUNK_RE.search(joined))


def _looks_like_new_header(joined: str, mapping: dict[int, str]) -> bool:
    low = joined.lower()
    hints = ("art no", "артикул", "quantity", "unit price", "amount", "description of", "net wt", "gross wt")
    hits = sum(1 for token in hints if token in low)
    return hits >= 3 and bool(mapping)


def _is_component_header(joined: str) -> bool:
    return bool(_COMPONENT_HEADER_RE.search(joined))


def _snapshot_line(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if k in _LINE_FIELDS and v not in (None, "")}


def _drop_note_description(fields: dict[str, Any]) -> None:
    text = str(fields.get("description") or "").strip()
    if text and " " not in text and _SKU_NOTE_RE.match(text):
        fields.pop("description", None)


def _row_merge_span(sheet: Sheet, r: int, c: int) -> tuple[int, int] | None:
    for r0, c0, r1, c1 in sheet.merges:
        if c0 <= c <= c1 and r0 <= r <= r1:
            return (r0, r1)
    return None


def _pack_group_id(sheet: Sheet, r: int, mapping: dict[int, str]) -> str | None:
    for field_key in ("net_weight", "gross_weight", "boxes", "volume"):
        col = next((c for c, name in mapping.items() if name == field_key), None)
        if col is None:
            continue
        span = _row_merge_span(sheet, r, col)
        if span and span[1] > span[0]:
            return f"{sheet.source}|{sheet.name}|{span[0]}:{span[1]}"
    return None


def _append_continuation(prev: Row, extra: dict[str, Any]) -> None:
    prev.lines.append(_snapshot_line(extra))
    for key in ("qty", "amount", "meters", "area", "net_weight", "gross_weight", "volume", "boxes", "rolls"):
        value = extra.get(key)
        if isinstance(value, (int, float)):
            prev.fields[key] = round((prev.fields.get(key) or 0) + value, 6)
    for key, value in extra.items():
        if key in prev.fields and prev.fields.get(key) not in (None, ""):
            continue
        if key in _LINE_FIELDS and value not in (None, ""):
            prev.fields[key] = value


def _strip_inherited(fields: dict[str, Any], inherited: set[str], keys: set[str]) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if not (k in keys and k in inherited)}


def _own(fields: dict[str, Any], inherited: set[str], key: str) -> bool:
    return key in fields and fields.get(key) not in (None, "") and key not in inherited


def _item_no(sheet: Sheet, r: int, mapping: dict[int, str]) -> int | None:
    # column 0 is usually the running number; take the left-most non-mapped numeric cell
    mapped = set(mapping)
    for c in range(min(3, sheet.ncols)):
        if c in mapped:
            continue
        v = sheet.grid[r][c] if c < len(sheet.grid[r]) else None
        if v is None:
            continue
        n = parse_number(v)
        if n is not None and float(n).is_integer() and 0 < n < 100000:
            return int(n)
    return None


def extract_sheet(sheet: Sheet) -> ExtractedSheet | None:
    header_row, mapping, _cols = find_header(sheet)
    if header_row < 0 or "article" not in set(mapping.values()) and "description" not in set(mapping.values()):
        return None
    text = _sheet_text(sheet, header_row)
    role = classify_role(text, set(mapping.values()), source=sheet.source)
    article_col = next((c for c, f in mapping.items() if f == "article"), None)
    desc_col = next((c for c, f in mapping.items() if f == "description"), None)
    key_col = article_col if article_col is not None else desc_col

    letterhead = [[_letterhead_cell(v) for v in sheet.grid[r]] for r in range(0, header_row)]
    ex = ExtractedSheet(
        name=sheet.name, source=sheet.source, role=role, mapping=mapping,
        header_text=text, letterhead=letterhead,
    )

    pending_children: list[Row] = []
    seen_articles: dict[str, int] = {}
    after_total = False
    component_mode = False
    active_mapping = mapping
    active_key_col = key_col

    def _bind_header(row_index: int) -> None:
        nonlocal active_mapping, active_key_col, article_col, desc_col
        cols = _build_columns(sheet, row_index)
        new_map = classify_columns(cols)
        if "article" in new_map.values() or "description" in new_map.values():
            active_mapping = new_map
            article_col = next((c for c, f in new_map.items() if f == "article"), None)
            desc_col = next((c for c, f in new_map.items() if f == "description"), None)
            active_key_col = article_col if article_col is not None else desc_col

    for r in range(header_row + 1, sheet.nrows):
        row = sheet.grid[r]
        joined = " ".join(_cell(v) for v in row).strip()
        if not joined:
            continue
        first_cells = " ".join(_cell(v) for v in row[:3])
        if _is_stop_row(joined, first_cells):
            _flush_children(ex, pending_children)
            pending_children = []
            if _TOTAL_RE.match(first_cells) or _TOTAL_RE.match(joined):
                after_total = True
                component_mode = False
                continue
            if _DETAIL_SECTION_RE.search(joined):
                after_total = True
                continue
            break
        if after_total and not component_mode:
            if _DETAIL_SECTION_RE.search(joined):
                continue
            if _looks_like_new_header(joined, active_mapping):
                if _is_component_header(joined):
                    _bind_header(r)
                    component_mode = True
                    continue
                break
            continue
        if component_mode and _looks_like_new_header(joined, active_mapping) and ex.component_rows:
            break
        if not after_total and ex.rows and _looks_like_new_header(joined, active_mapping):
            _flush_children(ex, pending_children)
            break
        if _is_letterhead_junk(joined):
            continue
        fields, inherited = _row_fields(sheet, r, active_mapping)
        _drop_note_description(fields)
        article_raw = (
            _cell(row[active_key_col]) if active_key_col is not None and active_key_col < len(row) else ""
        )
        article_inherited = bool(
            active_key_col is not None and (r, active_key_col) in sheet.inherited
        )
        own_article = bool(article_raw) and not article_inherited
        item_no = _item_no(sheet, r, active_mapping)
        own_qty = _own(fields, inherited, "qty") or _own(fields, inherited, "meters")

        if not article_raw and not any(k in fields for k in ("qty", "meters", "amount", "net_weight", "area")):
            continue

        bucket = ex.component_rows if component_mode else ex.rows
        prev = pending_children[-1] if pending_children else (bucket[-1] if bucket else None)

        if prev is not None and not own_article and not own_qty and not component_mode:
            packing_only = _strip_inherited(fields, inherited, _COMMERCIAL_FIELDS)
            packing_only = {
                k: v for k, v in packing_only.items() if k in _PACKING_FIELDS or k not in _COMMERCIAL_FIELDS
            }
            _append_continuation(prev, packing_only)
            continue

        if prev is not None and not own_article and own_qty and not component_mode:
            fields = _strip_inherited(fields, inherited, _PACKING_FIELDS)
            _append_continuation(prev, fields)
            continue

        if own_article and own_qty:
            fields = _strip_inherited(fields, inherited, _PACKING_FIELDS)

        article = article_raw
        if not article:
            continue

        pack_group = _pack_group_id(sheet, r, active_mapping)
        if pack_group:
            fields["_pack_group"] = pack_group

        is_group = bool(
            own_article
            and item_no is None
            and ("price" in fields or "amount" in fields)
            and pending_children
            and not component_mode
        )

        norm = normalize_article(article)
        row_obj = Row(
            article=article,
            normalized=norm,
            fields=fields,
            source=f"{sheet.source} · {sheet.name}",
            role=role,
            item_no=item_no,
            is_group=is_group,
            component=component_mode,
            lines=[_snapshot_line(fields)],
        )

        if is_group:
            _apply_group(pending_children, row_obj)
            _flush_children(ex, pending_children)
            pending_children = []
            continue

        if (
            not component_mode
            and role in {"invoice", "mixed", "goods"}
            and item_no is not None
            and "price" not in fields
            and "amount" not in fields
        ):
            pending_children.append(row_obj)
            continue

        _flush_children(ex, pending_children)
        pending_children = []
        bucket.append(row_obj)
        if not component_mode:
            seen_articles[norm] = seen_articles.get(norm, 0) + 1

    _flush_children(ex, pending_children)

    # detail sheet? a per-roll specification lists the same article on many lines.
    # Require both a high repeat AND a low unique/row ratio so that a normal
    # one-line-per-article invoice (Beijing) is never treated as detail.
    counts: dict[str, int] = {}
    for row_obj in ex.rows:
        counts[row_obj.normalized] = counts.get(row_obj.normalized, 0) + 1
    unique = len(counts)
    nrows = len(ex.rows)
    if nrows and unique and max(counts.values()) >= 3 and (nrows / unique) >= 2.0:
        ex.detail = True
        if role != "catalog":
            ex.role = "specification"
    return ex


def _accumulate(target: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if key in NUMERIC_FIELDS and isinstance(value, (int, float)):
            target[key] = round((target.get(key) or 0) + value, 6)
        elif key not in target or target.get(key) in (None, ""):
            target[key] = value


def _price_basis(fields: dict[str, Any]) -> float | None:
    for key in ("meters", "qty", "area"):
        v = fields.get(key)
        if isinstance(v, (int, float)) and v:
            return float(v)
    return None


def _apply_group(children: list[Row], group: Row) -> None:
    price = group.fields.get("price")
    if not children:
        return
    for child in children:
        if price is not None:
            child.fields.setdefault("price", price)
            basis = _price_basis(child.fields)
            if basis is not None and "amount" not in child.fields:
                child.fields["amount"] = round(basis * float(price), 2)
        # inherit HS / customs / currency / unit from the group when missing
        for key in ("hs_code", "customs_code", "currency", "unit"):
            if key in group.fields and key not in child.fields:
                child.fields[key] = group.fields[key]
        child.fields["_group"] = group.article


def _flush_children(ex: ExtractedSheet, children: list[Row]) -> None:
    for child in children:
        ex.rows.append(child)
