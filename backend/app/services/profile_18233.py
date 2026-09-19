"""Profile 18233 — dedicated parsers for Zhongfang Invoice / PL / Specification.

MVP brief (28.08.2026):
- Only fixed structures 626-1 / 626-2
- Match by normalized article/model (DESIGN / PRODUCT NAME)
- Keep Spec roll detail; aggregate for summary docs
- Invoice ↔ Spec: rolls, meters, width, area, price, amount
- Packing List ↔ Spec aggregates by design family: rolls, meters, area, NW, GW
- Acceptance: 626-1 → 13 positions; 626-2 → 17 positions
- Never silently replace disputed values
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.models.enums import DocType, ErrorSeverity, ErrorType
from app.parsing.classifier import classify_document
from app.parsing.excel_reader import load_excel_book
from app.parsing.normalize import normalize_article, normalize_text
from app.services.catalog import CatalogIndex
from app.services.field_map import (
    classify_header,
    is_factory_note,
    parse_number,
    repair_line_amount,
)
from app.services.reconcile import ReconciledItem, ValidationFlag, apply_catalog, _set_if_empty

HEADER_MARKERS = ("NO.", "DESIGN", "ROLL NO", "PRODUCT NAME")
GROUP_PREFIXES = (
    "SOFA FABRIC",
    "ARTIFICIAL LEATHER",
)
SKIP_DESIGNS = {"DESIGN", "TOTAL", "TOTAL:"}
METER_UNITS = {"m", "meter", "meters", "metres", "м", "м.п", "мп", "пог"}
PIECE_UNITS = {"pc", "pcs", "шт", "set", "sets", "pair", "pairs", "компл"}


def _cell(df: pd.DataFrame, row: int, col: int) -> Any:
    if row >= len(df) or col >= df.shape[1]:
        return None
    value = df.iat[row, col]
    if pd.isna(value):
        return None
    return value


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return parse_number(value)


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return normalize_text(str(value).replace("\n", " "))


def _design_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return normalize_text(str(value).replace("\n", " / "))


def _find_header_row(
    df: pd.DataFrame,
    markers: tuple[str, ...],
    *,
    min_hits: int = 2,
) -> int | None:
    limit = min(len(df), 40)
    for idx in range(limit):
        row_text = " | ".join(_text(df.iat[idx, c]).upper() for c in range(min(df.shape[1], 15)))
        hits = sum(1 for marker in markers if marker in row_text)
        if hits >= min_hits:
            return idx
    return None


def _is_group_design(design: str) -> bool:
    upper = design.upper()
    return any(upper.startswith(prefix) or f" {prefix}" in upper for prefix in GROUP_PREFIXES)


def _design_family(article: str) -> str:
    """Noble 110 -> NOBLE; NAPPA 000 -> NAPPA; Magic 01 -> MAGIC."""
    parts = article.strip().split()
    if not parts:
        return normalize_article(article)
    return normalize_article(parts[0])


def _extract_family_from_pl_design(design: str) -> str:
    """SOFA FABRIC / Sherlock -> SHERLOCK; ARTIFICIAL LEATHER NAPPA -> NAPPA."""
    _category, article = _split_category_article(design)
    seed = article or design
    if "\n" in seed:
        seed = seed.split("\n")[-1]
    cleaned = re.sub(r"(?i)sofa\s*fabric", " ", seed)
    cleaned = re.sub(r"(?i)artificial\s*leather", " ", cleaned)
    cleaned = re.sub(r"[/|]+", " ", cleaned)
    cleaned = normalize_text(cleaned)
    parts = cleaned.split()
    return normalize_article(parts[0] if parts else cleaned)


def _is_catalog_filename(filename: str) -> bool:
    name = (filename or "").lower()
    return any(token in name for token in ("сводная", "справочник", "catalog", "catalogue"))


def _split_category_article(design: str) -> tuple[str, str]:
    """'SOFA FABRIC / Sherlock' -> (SOFA FABRIC, Sherlock)."""
    text = normalize_text((design or "").replace("\n", " / "))
    if not text:
        return "", ""
    upper = text.upper()
    for prefix in GROUP_PREFIXES:
        if upper.startswith(prefix):
            rest = text[len(prefix) :].strip(" /|-")
            return prefix, rest or text
    parts = [part.strip() for part in re.split(r"\s*/\s*", text) if part.strip()]
    if len(parts) >= 2:
        right = parts[-1]
        left = " / ".join(parts[:-1])
        if left and right and len(right) >= 2:
            return left, right
    return "", text


def _unit_is_meters(unit: str | None) -> bool:
    text = (unit or "").strip().lower().replace(" ", "")
    if not text:
        return False
    return any(token == text or token in text for token in METER_UNITS)


def _unit_is_pieces(unit: str | None) -> bool:
    text = (unit or "").strip().lower().replace(" ", "")
    return any(token == text or text.startswith(token) for token in PIECE_UNITS)


def _header_colmap(df: pd.DataFrame, header_row: int) -> dict[str, int]:
    mapping: dict[str, int] = {}
    scores: dict[str, int] = {}
    for col in range(min(df.shape[1], 22)):
        title = _text(_cell(df, header_row, col))
        if not title:
            continue
        field = classify_header(title)
        low = title.lower()
        if field is None and ("package" in low or low in {"no.", "no", "№"}):
            field = "rolls" if "package" in low else None
        if not field:
            continue
        score = len(low)
        if field == "amount" and "amount" in low:
            score += 80
        if field == "area" and ("m2" in low or "m²" in low or "кв" in low):
            score += 80
        if field == "price" and "price" in low:
            score += 40
        if field not in mapping or score > scores[field]:
            mapping[field] = col
            scores[field] = score
    return mapping


def _mapped_cell(df: pd.DataFrame, row: int, colmap: dict[str, int], field: str) -> Any:
    idx = colmap.get(field)
    if idx is None:
        return None
    return _cell(df, row, idx)


def _qty_meters(qty: float | None, meters: float | None, unit: str | None) -> tuple[float | None, float | None]:
    if meters is not None:
        commercial = qty if qty is not None else meters
        return commercial, meters
    if qty is None:
        return None, None
    if _unit_is_pieces(unit):
        return qty, None
    if _unit_is_meters(unit) or not unit:
        return qty, qty
    return qty, None


def _guess_unit_from_row(df: pd.DataFrame, row: int, colmap: dict[str, int]) -> str | None:
    unit = _text(_mapped_cell(df, row, colmap, "unit")) or None
    if unit:
        return unit
    mapped_idx = set(colmap.values())
    for col in range(min(df.shape[1], 12)):
        if col in mapped_idx:
            continue
        text = _text(_cell(df, row, col))
        if not text or len(text) > 12:
            continue
        low = text.lower()
        if _unit_is_meters(text) or _unit_is_pieces(text) or low in {"kg", "кg"}:
            return text
    return None


@dataclass
class Parsed18233Bundle:
    subkit: str | None
    invoice_rows: list[dict[str, Any]] = field(default_factory=list)
    packing_groups: list[dict[str, Any]] = field(default_factory=list)
    spec_rolls: list[dict[str, Any]] = field(default_factory=list)
    spec_aggregates: dict[str, dict[str, Any]] = field(default_factory=dict)
    header_hints: dict[str, Any] = field(default_factory=dict)


def detect_subkit(paths: list[str | Path]) -> str | None:
    blob = " ".join(Path(p).name for p in paths)
    match = re.search(r"\b(\d{2,4}-\d)\b", blob)
    return match.group(1) if match else None


def _is_excel_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in {".xlsx", ".xls", ".xlsm"}


def _iter_excel_sheets(path: str | Path) -> list[tuple[str, pd.DataFrame]]:
    if not _is_excel_path(path):
        return []
    try:
        book = load_excel_book(path)
    except Exception:
        return []
    if isinstance(book, dict):
        return [(str(name), frame) for name, frame in book.items()]
    return [("Sheet1", book)]


def _merge_unique(primary: list[dict[str, Any]], extra: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    have = {row.get(key) for row in primary if row.get(key)}
    out = list(primary)
    for row in extra:
        item_key = row.get(key)
        if not item_key or item_key in have:
            continue
        out.append(row)
        have.add(item_key)
    return out


def parse_invoice(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    best: list[dict[str, Any]] = []
    hints: dict[str, Any] = {}
    extras: list[dict[str, Any]] = []
    for _name, df in _iter_excel_sheets(path):
        products, sheet_hints = _parse_invoice_sheet(df)
        if len(products) > len(best):
            extras.extend(best)
            best, hints = products, sheet_hints
        else:
            extras.extend(products)
            if not hints:
                hints = sheet_hints
    return _merge_unique(best, extras, "normalized_article"), hints


def _parse_invoice_sheet(
    df: pd.DataFrame,
    *,
    require_header: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    header = _find_header_row(
        df,
        ("NO.", "DESIGN", "ROLLS", "METERS", "QUANTITY", "PACKAGES", "AMOUNT"),
        min_hits=3 if require_header else 2,
    )
    if header is None:
        header = _find_header_row(df, ("NO.", "DESIGN", "H.S", "QUANTITY"), min_hits=3)
    if header is None:
        return [], {}
    colmap = _header_colmap(df, header)
    hints: dict[str, Any] = {}
    # crude header scrape
    for r in range(0, header):
        for c in range(min(df.shape[1], 10)):
            cell = _text(_cell(df, r, c))
            if cell.upper().startswith("INV.NO") or cell.upper() == "INV.NO.":
                hints["invoice_no"] = _text(_cell(df, r, c + 1)) or hints.get("invoice_no")
            if "CONTRACT" in cell.upper():
                hints["contract_no"] = cell
            if cell.upper().startswith("DATE"):
                hints["date"] = _text(_cell(df, r, c + 1)) or hints.get("date")
            if cell.upper().startswith("BUYER"):
                hints["buyer"] = cell

    design_col = colmap.get("article", 1)
    raw_rows: list[dict[str, Any]] = []
    for r in range(header + 1, len(df)):
        design = _design_text(_cell(df, r, design_col))
        if not design:
            continue
        if design.upper().startswith("TOTAL"):
            break
        no = _cell(df, r, 0)
        unit = _guess_unit_from_row(df, r, colmap)
        qty = _num(_mapped_cell(df, r, colmap, "qty"))
        meters = _num(_mapped_cell(df, r, colmap, "meters"))
        qty, meters = _qty_meters(qty, meters, unit)
        rolls = _num(_mapped_cell(df, r, colmap, "rolls"))
        if rolls is None:
            rolls = _num(_mapped_cell(df, r, colmap, "boxes"))
        category, article_from_design = _split_category_article(design)
        row = {
            "row_index": r,
            "no": no,
            "design": design,
            "category": category or None,
            "hs_code": _text(_mapped_cell(df, r, colmap, "hs_code")) or _text(_cell(df, r, 2)) or None,
            "rolls": rolls,
            "width": _num(_mapped_cell(df, r, colmap, "width")),
            "area": _num(_mapped_cell(df, r, colmap, "area")),
            "qty": qty,
            "meters": meters,
            "unit": unit,
            "price": _num(_mapped_cell(df, r, colmap, "price")),
            "amount": _num(_mapped_cell(df, r, colmap, "amount")),
            "description": _text(_mapped_cell(df, r, colmap, "description")) or category or None,
            "is_group": _is_group_design(design) or no is None,
        }
        raw_rows.append(row)

    products: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for row in raw_rows:
        if row["is_group"]:
            category, _grouped_article = _split_category_article(row["design"])
            for prod in pending:
                if prod.get("price") is None and row.get("price") is not None:
                    prod["price"] = row["price"]
                    prod["price_from_group"] = row["design"]
                repair_line_amount(prod)
                if not prod.get("description") and (category or row.get("description")):
                    note = category or row.get("description")
                    if not is_factory_note(note):
                        prod["description"] = note
            pending = []
            continue
        if _text(row["design"]).upper() in SKIP_DESIGNS:
            continue
        # Product lines must have a numeric NO (skip header leftovers)
        if not isinstance(row["no"], (int, float)) and not str(row["no"] or "").isdigit():
            continue
        category, article = _split_category_article(row["design"])
        row["article"] = article
        row["normalized_article"] = normalize_article(article)
        row["design_family"] = _design_family(article)
        if category and not row.get("description"):
            row["description"] = category
        products.append(row)
        pending.append(row)

    for prod in pending:
        repair_line_amount(prod)

    return products, hints


def parse_packing_list(path: str | Path) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    for _name, df in _iter_excel_sheets(path):
        groups = _parse_packing_sheet(df)
        if len(groups) > len(best):
            extras.extend(best)
            best = groups
        else:
            extras.extend(groups)
    return _merge_unique(best, extras, "normalized_article")


def _parse_packing_sheet(
    df: pd.DataFrame,
    *,
    require_header: bool = False,
) -> list[dict[str, Any]]:
    header = _find_header_row(
        df,
        ("DESIGN", "ROLLS", "NET", "GROSS", "PACKAGES", "QUANTITY"),
        min_hits=3 if require_header else 2,
    )
    if header is None:
        return []
    colmap = _header_colmap(df, header)
    design_col = colmap.get("article", 1)
    groups: list[dict[str, Any]] = []
    for r in range(header + 1, len(df)):
        design = _design_text(_cell(df, r, design_col))
        if not design:
            continue
        if design.upper().startswith("TOTAL"):
            break
        category, article = _split_category_article(design)
        family = _extract_family_from_pl_design(design)
        unit = _guess_unit_from_row(df, r, colmap)
        qty = _num(_mapped_cell(df, r, colmap, "qty"))
        meters = _num(_mapped_cell(df, r, colmap, "meters"))
        qty, meters = _qty_meters(qty, meters, unit)
        rolls = _num(_mapped_cell(df, r, colmap, "rolls"))
        if rolls is None:
            rolls = _num(_mapped_cell(df, r, colmap, "boxes"))
        gm = _num(_mapped_cell(df, r, colmap, "gm"))
        if gm is None and 2 not in set(colmap.values()):
            gm = _num(_cell(df, r, 2))
        groups.append(
            {
                "row_index": r,
                "design": design,
                "article": article,
                "category": category or None,
                "description": None if (category and _is_group_design(category)) else (category or None),
                "design_family": family,
                "normalized_family": family,
                "normalized_article": normalize_article(article) if article else family,
                "gm": gm,
                "rolls": rolls,
                "qty": qty,
                "meters": meters,
                "unit": unit,
                "net_weight": _num(_mapped_cell(df, r, colmap, "net_weight")),
                "gross_weight": _num(_mapped_cell(df, r, colmap, "gross_weight")),
                "area": _num(_mapped_cell(df, r, colmap, "area")),
            }
        )
    return groups


def _looks_aggregated(rows: list[dict[str, Any]]) -> bool:
    if len(rows) < 2:
        return True
    keys = [row.get("normalized_article") for row in rows]
    return len(set(keys)) == len(keys)


def _parse_specification_sheet(
    df: pd.DataFrame,
    *,
    require_header: bool = False,
) -> list[dict[str, Any]]:
    header = _find_header_row(df, ("ROLL NO", "PRODUCT", "METERS", "WIDTH"), min_hits=3 if require_header else 2)
    if header is None:
        header = _find_header_row(df, ("PRODUCT NAME", "QUANTITY", "N.W", "G.W"), min_hits=3)
    if header is None:
        header = _find_header_row(df, ("ART", "PRODUCT", "METERS", "CUSTOMS"), min_hits=3)
    if header is None:
        return []
    colmap = _header_colmap(df, header)
    article_col = colmap.get("article", 2)
    rolls: list[dict[str, Any]] = []
    for r in range(header + 1, len(df)):
        product = _text(_cell(df, r, article_col))
        if not product:
            continue
        if product.upper().startswith("TOTAL"):
            break
        unit = _guess_unit_from_row(df, r, colmap)
        qty = _num(_mapped_cell(df, r, colmap, "qty"))
        meters = _num(_mapped_cell(df, r, colmap, "meters"))
        qty, meters = _qty_meters(qty, meters, unit)
        description = _text(_mapped_cell(df, r, colmap, "description")) or None
        if description and is_factory_note(description):
            description = None
        roll = {
            "row_index": r,
            "roll_no": _text(_cell(df, r, 0)) or None,
            "date": _text(_mapped_cell(df, r, colmap, "date")) or _text(_cell(df, r, 1)) or None,
            "article": product,
            "normalized_article": normalize_article(product),
            "design_family": _design_family(product),
            "lot_no": _text(_cell(df, r, 3)) if 3 not in set(colmap.values()) else None,
            "price": _num(_mapped_cell(df, r, colmap, "price")),
            "qty": qty,
            "meters": meters,
            "unit": unit,
            "width": _num(_mapped_cell(df, r, colmap, "width")),
            "area": _num(_mapped_cell(df, r, colmap, "area")),
            "net_weight": _num(_mapped_cell(df, r, colmap, "net_weight")),
            "gross_weight": _num(_mapped_cell(df, r, colmap, "gross_weight")),
            "description": description,
        }
        if roll["area"] is None and roll["meters"] is not None and roll["width"] is not None:
            roll["area"] = round(float(roll["meters"]) * float(roll["width"]), 3)
            roll["area_calculated"] = True
        rolls.append(roll)
    return rolls


def parse_specification(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    best: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    for _name, df in _iter_excel_sheets(path):
        rolls = _parse_specification_sheet(df)
        if len(rolls) > len(best):
            extras.extend(best)
            best = rolls
        else:
            extras.extend(rolls)
    rolls = _merge_unique(best, extras, "normalized_article") if _looks_aggregated(best) else best
    # Roll-level spec: keep the richest sheet only so meters are not doubled
    if not _looks_aggregated(best):
        rolls = best
    return _aggregates_from_rolls(rolls)


def parse_bundle(paths: list[str | Path]) -> Parsed18233Bundle:
    named: dict[str, Path] = {}
    for path in paths:
        p = Path(path)
        if _is_catalog_filename(p.name):
            continue
        kind = _filename_kind(p.name)
        if kind:
            named[kind] = p

    bundle = Parsed18233Bundle(subkit=detect_subkit(paths))
    if "invoice" in named and _is_excel_path(named["invoice"]):
        bundle.invoice_rows, bundle.header_hints = parse_invoice(named["invoice"])
    if "packing" in named and _is_excel_path(named["packing"]):
        bundle.packing_groups = parse_packing_list(named["packing"])
    if "specification" in named and _is_excel_path(named["specification"]):
        bundle.spec_rolls, bundle.spec_aggregates = parse_specification(named["specification"])

    for path in paths:
        p = Path(path)
        if p.suffix.lower() not in {".xlsx", ".xls", ".xlsm"}:
            continue
        if _is_catalog_filename(p.name):
            continue
        try:
            sheets = _iter_excel_sheets(p)
        except Exception:
            continue
        for sheet_name, df in sheets:
            kind = _kind_from_sheet(sheet_name, df)
            if kind is None:
                continue
            if kind == "invoice" and named.get("invoice") == p:
                continue
            if kind == "packing" and named.get("packing") == p:
                continue
            if kind == "specification" and named.get("specification") == p:
                continue
            if kind == "invoice":
                products, hints = _parse_invoice_sheet(df, require_header=True)
                if products:
                    bundle.invoice_rows = _merge_unique(bundle.invoice_rows, products, "normalized_article")
                    if hints and not bundle.header_hints:
                        bundle.header_hints = hints
            elif kind == "packing":
                groups = _parse_packing_sheet(df, require_header=True)
                if groups:
                    bundle.packing_groups = _merge_unique(bundle.packing_groups, groups, "normalized_family")
            elif kind == "specification":
                rolls = _parse_specification_sheet(df, require_header=True)
                if rolls:
                    bundle.spec_rolls.extend(rolls)

    if bundle.spec_rolls and not bundle.spec_aggregates:
        bundle.spec_rolls, bundle.spec_aggregates = _aggregates_from_rolls(bundle.spec_rolls)
    return bundle


def _filename_kind(filename: str) -> str | None:
    if _is_catalog_filename(filename):
        return None
    name = filename.upper()
    stem = Path(filename).stem.upper()
    if "INVOICE" in name or "ИНВОЙС" in name:
        return "invoice"
    if "-PL" in name or "PACKING" in name or name.endswith("PL.XLSX") or "ПАКИНГ" in name or "УПАКОВ" in name:
        return "packing"
    if "SPEC" in name or "СПЕЦИФ" in name:
        return "specification"
    if stem.endswith("_CI") or stem.endswith("-CI"):
        return "invoice"
    if stem.endswith("_PL") or stem.endswith("-PL"):
        return "packing"
    return None


def _kind_from_sheet(sheet_name: str, df: pd.DataFrame) -> str | None:
    sample = df.head(40).fillna("").astype(str)
    preview = f"{sheet_name} " + " ".join(sample.to_numpy().ravel().tolist())
    result = classify_document(
        filename="",
        text=preview,
        sheet_names=[sheet_name],
        use_filename=False,
    )
    mapping = {
        DocType.INVOICE: "invoice",
        DocType.PACKING_LIST: "packing",
        DocType.SPECIFICATION: "specification",
    }
    return mapping.get(result.doc_type)


def _aggregates_from_rolls(rolls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    aggregates: dict[str, dict[str, Any]] = {}
    for roll in rolls:
        key = roll["normalized_article"]
        agg = aggregates.setdefault(
            key,
            {
                "article": roll["article"],
                "normalized_article": key,
                "design_family": roll["design_family"],
                "rolls": 0,
                "qty": 0.0,
                "meters": 0.0,
                "area": 0.0,
                "net_weight": 0.0,
                "gross_weight": 0.0,
                "price": None,
                "width": None,
                "unit": None,
                "description": None,
            },
        )
        agg["rolls"] += 1
        agg["qty"] += float(roll.get("qty") or 0)
        agg["meters"] += float(roll.get("meters") or 0)
        agg["area"] += float(roll.get("area") or 0)
        agg["net_weight"] += float(roll.get("net_weight") or 0)
        agg["gross_weight"] += float(roll.get("gross_weight") or 0)
        if roll.get("price") is not None:
            agg["price"] = roll.get("price")
        if roll.get("width") is not None:
            agg["width"] = roll.get("width")
        if roll.get("unit") and not agg.get("unit"):
            agg["unit"] = roll.get("unit")
        if roll.get("description") and not agg.get("description") and not is_factory_note(roll.get("description")):
            agg["description"] = roll.get("description")
    for agg in aggregates.values():
        agg["qty"] = round(agg.get("qty") or 0, 4)
        agg["meters"] = round(agg["meters"], 4)
        agg["area"] = round(agg["area"], 4)
        agg["net_weight"] = round(agg["net_weight"], 4)
        agg["gross_weight"] = round(agg["gross_weight"], 4)
        if not agg["meters"] and agg["qty"] and not _unit_is_pieces(agg.get("unit")):
            agg["meters"] = agg["qty"]
    return rolls, aggregates


def _empty_weight(value: Any) -> bool:
    return value in (None, "", 0, 0.0)


def _distribute_pl_weights(group_items: list[ReconciledItem], pl: dict[str, Any]) -> None:
    """Packing-list family totals are the commercial weights. Split by meters when needed."""
    net = pl.get("net_weight")
    gross = pl.get("gross_weight")
    if _empty_weight(net) and _empty_weight(gross):
        return
    weights = [
        float((item.packing_data or {}).get("meters") or (item.commercial_data or {}).get("qty") or 0)
        for item in group_items
    ]
    total = sum(weights)
    if total <= 0:
        return
    for item, share_base in zip(group_items, weights, strict=True):
        share = share_base / total if total else 0
        if not _empty_weight(net):
            spec_net = (item.packing_data or {}).get("net_weight")
            item.packing_data["spec_net_weight"] = spec_net
            item.packing_data["net_weight"] = round(float(net) * share, 2)
        if not _empty_weight(gross):
            spec_gw = (item.packing_data or {}).get("gross_weight")
            item.packing_data["spec_gross_weight"] = spec_gw
            item.packing_data["gross_weight"] = round(float(gross) * share, 2)
        item.packing_data["distributed_from_packing"] = True


def _nearly_equal(a: Any, b: Any, tol: float = 0.05) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a).strip().upper() == str(b).strip().upper()


def _compare(
    item: ReconciledItem,
    field_name: str,
    left: Any,
    right: Any,
    left_name: str,
    right_name: str,
) -> None:
    if left is None or right is None:
        if left is None and right is None:
            return
        item.flags.append(
            ValidationFlag(
                field_name=field_name,
                error_type=ErrorType.MISSING_PAIR,
                severity=ErrorSeverity.YELLOW,
                details={left_name: left, right_name: right},
                message=f"Пустое значение «{field_name}» в {left_name if left is None else right_name}",
            )
        )
        return
    if not _nearly_equal(left, right):
        item.flags.append(
            ValidationFlag(
                field_name=field_name,
                error_type=ErrorType.MISMATCH,
                severity=ErrorSeverity.RED,
                details={left_name: left, right_name: right},
                message=f"Расхождение «{field_name}»: {left_name}={left}, {right_name}={right}",
            )
        )


def reconcile_18233(
    bundle: Parsed18233Bundle,
    catalog: CatalogIndex | None = None,
) -> list[ReconciledItem]:
    import json
    from pathlib import Path

    ref_path = Path(__file__).resolve().parent / "data" / "reference_18233.json"
    reference: dict[str, dict] = {}
    if ref_path.exists():
        reference = json.loads(ref_path.read_text(encoding="utf-8"))

    items: list[ReconciledItem] = []
    inv_by_key = {normalize_article(r["article"]): r for r in bundle.invoice_rows}
    spec_by_key = bundle.spec_aggregates
    pl_by_family: dict[str, list[dict[str, Any]]] = {}
    pl_by_article: dict[str, list[dict[str, Any]]] = {}
    for g in bundle.packing_groups:
        pl_by_family.setdefault(g["normalized_family"], []).append(g)
        art_key = g.get("normalized_article") or ""
        if art_key:
            pl_by_article.setdefault(art_key, []).append(g)

    # Invoice is the working table. Spec rolls stay as confirmation, not extra rows.
    if bundle.invoice_rows:
        ordered_keys: list[str] = [normalize_article(r["article"]) for r in bundle.invoice_rows]
    else:
        ordered_keys = list(spec_by_key)

    for key in ordered_keys:
        inv = inv_by_key.get(key)
        spec = spec_by_key.get(key)
        article = (inv or spec or {}).get("article") or key
        item = ReconciledItem(
            article=article,
            model=article,
            normalized_article=key,
            invoice_subkit=bundle.subkit,
        )
        if inv:
            item.source_traces["invoice"] = inv
            qty = inv.get("qty")
            meters = inv.get("meters")
            unit = inv.get("unit") or ("meters" if meters is not None and qty is None else None)
            if qty is None:
                qty = meters
            item.commercial_data.update(
                {
                    "qty": qty,
                    "unit": unit or "meters",
                    "price": inv.get("price"),
                    "amount": inv.get("amount"),
                    "currency": "CNY",
                    "invoice_subkit": bundle.subkit,
                }
            )
            item.packing_data.update(
                {
                    "rolls": inv.get("rolls"),
                    "meters": meters,
                    "area": inv.get("area"),
                    "width": inv.get("width"),
                }
            )
            item.customs_data["hs_code"] = inv.get("hs_code")
            if inv.get("description") and not is_factory_note(inv.get("description")):
                item.customs_data["description"] = inv.get("description")
            probe = dict(item.commercial_data)
            probe["area"] = item.packing_data.get("area")
            probe["meters"] = item.packing_data.get("meters")
            repair_line_amount(probe)
            item.commercial_data["amount"] = probe.get("amount")
        if spec:
            item.source_traces["specification"] = {
                k: v for k, v in spec.items() if k != "raw"
            }
            item.packing_data["rolls"] = item.packing_data.get("rolls") or spec.get("rolls")
            _fill = item.packing_data
            for field_name in ("meters", "area", "width", "net_weight", "gross_weight"):
                current = _fill.get(field_name)
                incoming = spec.get(field_name)
                if incoming in (None, "", 0, 0.0):
                    continue
                if current in (None, "", 0, 0.0):
                    _fill[field_name] = incoming
            # эталон uses 2 decimal weights
            if _fill.get("net_weight") is not None:
                _fill["net_weight"] = round(float(_fill["net_weight"]), 2)
            if _fill.get("gross_weight") is not None:
                _fill["gross_weight"] = round(float(_fill["gross_weight"]), 2)
            if item.commercial_data.get("price") is None:
                item.commercial_data["price"] = spec.get("price")
            if item.commercial_data.get("qty") is None and spec.get("qty"):
                item.commercial_data["qty"] = spec.get("qty")
            if not item.commercial_data.get("unit") and spec.get("unit"):
                item.commercial_data["unit"] = spec.get("unit")
            if spec.get("description") and not is_factory_note(spec.get("description")):
                _set_if_empty(item.customs_data, "description", spec.get("description"))
            probe = dict(item.commercial_data)
            probe["area"] = item.packing_data.get("area")
            probe["meters"] = item.packing_data.get("meters") or spec.get("meters")
            repair_line_amount(probe)
            item.commercial_data["amount"] = probe.get("amount")
            if item.commercial_data.get("amount") is None and item.commercial_data.get("price") and (
                item.packing_data.get("meters") or spec.get("meters") or item.commercial_data.get("qty")
            ):
                qty_for_amount = float(
                    item.packing_data.get("meters")
                    or spec.get("meters")
                    or item.commercial_data.get("qty")
                    or 0
                )
                item.commercial_data["amount"] = round(
                    qty_for_amount * float(item.commercial_data["price"]), 2
                )
                item.commercial_data["amount_calculated"] = True

        # Reference fill from эталон (exact article only) — operator may still override
        ref = reference.get(key)
        if ref:
            item.source_traces["reference"] = ref
            if not item.customs_data.get("tnved_code"):
                item.customs_data["tnved_code"] = ref.get("tnved_code")
            if not item.customs_data.get("description"):
                item.customs_data["description"] = ref.get("description")
            if not item.customs_data.get("country"):
                item.customs_data["country"] = ref.get("country")
            if not item.customs_data.get("manufacturer"):
                item.customs_data["manufacturer"] = ref.get("manufacturer")
            if not item.customs_data.get("hs_code") and ref.get("hs_code"):
                item.customs_data["hs_code"] = ref.get("hs_code")
        elif catalog is None:
            item.flags.append(
                ValidationFlag(
                    field_name="tnved_code",
                    error_type=ErrorType.CATALOG_NOT_FOUND,
                    severity=ErrorSeverity.YELLOW,
                    details={"normalized_article": key},
                    message="Нет эталонного описания или кода ТН ВЭД. Заполните вручную.",
                )
            )
        if catalog is not None:
            apply_catalog([item], catalog)

        family = _design_family(article)
        pl_groups = pl_by_article.get(key) or []
        if not pl_groups and key:
            pl_groups = [
                g
                for g in bundle.packing_groups
                if key in (g.get("normalized_article") or "")
                or normalize_article(article) in (g.get("normalized_article") or "")
            ]
        matched_by_article = bool(pl_groups)
        if not pl_groups:
            pl_groups = pl_by_family.get(family) or []
        if pl_groups:
            if len(pl_groups) == 1:
                item.source_traces["packing_list_group"] = pl_groups[0]
            else:
                merged = {
                    "design": pl_groups[0].get("design"),
                    "design_family": family,
                    "normalized_family": family,
                    "gm": pl_groups[0].get("gm"),
                    "rolls": sum(float(g.get("rolls") or 0) for g in pl_groups),
                    "meters": sum(float(g.get("meters") or 0) for g in pl_groups),
                    "net_weight": sum(float(g.get("net_weight") or 0) for g in pl_groups),
                    "gross_weight": sum(float(g.get("gross_weight") or 0) for g in pl_groups),
                    "area": sum(float(g.get("area") or 0) for g in pl_groups),
                    "parts": pl_groups,
                }
                item.source_traces["packing_list_group"] = merged
            if matched_by_article:
                pl = {
                    "rolls": sum(float(g.get("rolls") or 0) for g in pl_groups),
                    "meters": sum(float(g.get("meters") or 0) for g in pl_groups),
                    "qty": sum(float(g.get("qty") or g.get("meters") or 0) for g in pl_groups),
                    "net_weight": sum(float(g.get("net_weight") or 0) for g in pl_groups),
                    "gross_weight": sum(float(g.get("gross_weight") or 0) for g in pl_groups),
                    "area": sum(float(g.get("area") or 0) for g in pl_groups),
                }
                for field_name in ("rolls", "meters", "net_weight", "gross_weight", "area"):
                    if item.packing_data.get(field_name) in (None, "", 0, 0.0) and pl.get(field_name):
                        item.packing_data[field_name] = pl[field_name]
                for g in pl_groups:
                    if g.get("description"):
                        _set_if_empty(item.customs_data, "description", g.get("description"))
                        break
                if inv:
                    _compare(item, "rolls", inv.get("rolls"), pl.get("rolls"), "invoice", "packing")
                    left_qty = inv.get("meters") if inv.get("meters") is not None else inv.get("qty")
                    right_qty = pl.get("meters") if pl.get("meters") else pl.get("qty")
                    if left_qty is not None and right_qty is not None:
                        _compare(item, "qty", left_qty, right_qty, "invoice", "packing")

        if not item.customs_data.get("tnved_code"):
            item.flags.append(
                ValidationFlag(
                    field_name="tnved_code",
                    error_type=ErrorType.MISSING_PAIR,
                    severity=ErrorSeverity.YELLOW,
                    details={},
                    message="Пустой таможенный код",
                )
            )
        if not (
            item.customs_data.get("description")
            or item.customs_data.get("description_ru")
            or item.customs_data.get("description_en")
        ):
            item.flags.append(
                ValidationFlag(
                    field_name="description",
                    error_type=ErrorType.MISSING_PAIR,
                    severity=ErrorSeverity.YELLOW,
                    details={},
                    message="Пустое описание товара",
                )
            )

        if inv and spec:
            _compare(item, "rolls", inv.get("rolls"), spec.get("rolls"), "invoice", "specification")
            _compare(item, "meters", inv.get("meters"), spec.get("meters"), "invoice", "specification")
            _compare(item, "width", inv.get("width"), spec.get("width"), "invoice", "specification")
            _compare(item, "area", inv.get("area"), spec.get("area"), "invoice", "specification")
            if inv.get("price") is not None and spec.get("price") is not None:
                _compare(item, "price", inv.get("price"), spec.get("price"), "invoice", "specification")
        elif inv and not spec:
            item.flags.append(
                ValidationFlag(
                    field_name="article",
                    error_type=ErrorType.MISSING_PAIR,
                    severity=ErrorSeverity.YELLOW,
                    details={"missing_in": "specification"},
                    message="Позиция есть в Invoice, нет в Specification",
                )
            )
        elif spec and not inv:
            item.flags.append(
                ValidationFlag(
                    field_name="article",
                    error_type=ErrorType.MISSING_PAIR,
                    severity=ErrorSeverity.YELLOW,
                    details={"missing_in": "invoice"},
                    message="Позиция есть в Specification, нет в Invoice",
                )
            )

        items.append(item)

    family_items: dict[str, list[ReconciledItem]] = {}
    for item in items:
        family_items.setdefault(_design_family(item.article or item.normalized_article), []).append(item)

    for family, group_items in family_items.items():
        pl_groups = pl_by_family.get(family) or []
        if not pl_groups:
            for item in group_items:
                if item.source_traces.get("packing_list_group"):
                    continue
                item.flags.append(
                    ValidationFlag(
                        field_name="design_family",
                        error_type=ErrorType.MISSING_PAIR,
                        severity=ErrorSeverity.YELLOW,
                        details={"family": family},
                        message=f"Нет группы Packing List для семейства {family}",
                    )
                )
            continue
        if group_items and all(
            (item.source_traces.get("packing_list_group") or {}).get("normalized_article")
            == item.normalized_article
            for item in group_items
        ):
            continue
        pl = {
            "rolls": sum(float(g.get("rolls") or 0) for g in pl_groups),
            "meters": sum(float(g.get("meters") or 0) for g in pl_groups),
            "area": sum(float(g.get("area") or 0) for g in pl_groups),
            "net_weight": sum(float(g.get("net_weight") or 0) for g in pl_groups),
            "gross_weight": sum(float(g.get("gross_weight") or 0) for g in pl_groups),
        }
        _distribute_pl_weights(group_items, pl)
        sums = {
            "rolls": sum(float((i.packing_data or {}).get("rolls") or 0) for i in group_items),
            "meters": sum(float((i.packing_data or {}).get("meters") or 0) for i in group_items),
            "area": sum(float((i.packing_data or {}).get("area") or 0) for i in group_items),
            "net_weight": sum(float((i.packing_data or {}).get("net_weight") or 0) for i in group_items),
            "gross_weight": sum(float((i.packing_data or {}).get("gross_weight") or 0) for i in group_items),
        }
        for field_name in ("rolls", "meters", "area", "net_weight", "gross_weight"):
            left = pl.get(field_name)
            right = round(sums[field_name], 4)
            if left is None:
                continue
            if not _nearly_equal(left, right, tol=0.1):
                for item in group_items:
                    item.flags.append(
                        ValidationFlag(
                            field_name=field_name,
                            error_type=ErrorType.MISMATCH,
                            severity=ErrorSeverity.RED,
                            details={
                                "packing_list": left,
                                "spec_family_sum": right,
                                "family": family,
                            },
                            message=(
                                f"Сумма Specification по семейству {family} "
                                f"не сходится с Packing List по «{field_name}»"
                            ),
                        )
                    )

    for fam, groups in pl_by_family.items():
        if fam in family_items:
            continue
        if bundle.invoice_rows:
            continue
        orphan = ReconciledItem(
            article=groups[0].get("design"),
            model=None,
            normalized_article=fam,
            invoice_subkit=bundle.subkit,
            source_traces={"packing_list_group": groups[0]},
        )
        orphan.flags.append(
            ValidationFlag(
                field_name="article",
                error_type=ErrorType.MISSING_PAIR,
                severity=ErrorSeverity.YELLOW,
                details={"family": fam},
                message="Группа Packing List без позиций Invoice/Specification",
            )
        )
        items.append(orphan)

    return items
