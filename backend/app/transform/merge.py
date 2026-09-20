"""Merge many sheets into one canonical per-article table.

Merge key is a tolerant `match_key` that ignores spaces, hyphens, dots and case, so
`MO SHO-01` == `MOSHO-01`, `VA-01` == `VA01`, `VA 04` == `VA04`. Display article keeps
the nicest spelling we saw (invoice first).

Per-field source priority:
  commercial (price/amount/qty/unit/currency) -> invoice/goods
  packing    (net/gross) -> packing list is authoritative for the final export;
               sender Specification is used only to share a family total across
               colour/article children (and as fallback when PL has no weight).
               Typical sender-spec vs PL drift is about ±0.2 kg; larger gaps are flagged.
  packing    (measurement/volume/boxes/gm/pcs) -> packing, else invoice, else spec
  quantities (rolls/meters/area/width)                    -> invoice, else packing, else spec
  customs    (hs/customs/description/manufacturer/country)-> catalog, else invoice/spec

Specification (per-roll detail) sheets are aggregated by summation. Hangzhou packing is
family-level ("SOFA FABRIC Noble"): its net/gross are split across the invoice children
by each child's share of the sender-spec weight (fallback: meters / area / qty).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.parsing.normalize import normalize_text
from app.transform.extract import ExtractedSheet, Row, split_design

_NON_ALNUM = re.compile(r"[^0-9A-Za-zА-Яа-яЁё]+")
_GROUP_PREFIX = re.compile(r"^(sofa\s*fabric|artificial\s*leather|genuine\s*leather|pu\s*leather)\s*", re.IGNORECASE)

_SUM_FIELDS = ("rolls", "boxes", "meters", "area", "net_weight", "gross_weight", "volume", "qty")
_PACKING_OVERWRITE = ("net_weight", "gross_weight")
_PACKING_FILL = (
    "measurement",
    "volume",
    "boxes",
    "gm",
    "pcs_per_carton",
    "rolls",
    "meters",
    "area",
    "width",
    "color",
)

# Sender Specification vs Packing List: customer says the usual drift is about ±0.2 kg.
# Packing remains the authority for the finished export; beyond the band we still correct
# to packing and raise a yellow flag so the operator sees the gap.
WEIGHT_TOLERANCE_KG = 0.2


def match_key(article: str | None) -> str:
    text = normalize_text(article).upper()
    return _NON_ALNUM.sub("", text)


def lot_token(fields: dict[str, Any] | None) -> str:
    """Identity of a commercial lot: own qty (or meters). Empty if unknown."""
    data = fields or {}
    qty = data.get("qty")
    if qty is None:
        qty = data.get("meters")
    if isinstance(qty, (int, float)):
        return f"{round(float(qty), 6):g}"
    return ""


def _family_model(article: str) -> str:
    stripped = _GROUP_PREFIX.sub("", normalize_text(article))
    return stripped.strip()


@dataclass
class CanonicalItem:
    article: str
    key: str
    fields: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    flags: list[dict[str, str]] = field(default_factory=list)
    lines: list[dict[str, Any]] = field(default_factory=list)

    def get(self, name: str) -> Any:
        return self.fields.get(name)


def _prefer(item: CanonicalItem, key: str, value: Any, *, overwrite: bool = False) -> None:
    if value in (None, ""):
        return
    if overwrite or item.fields.get(key) in (None, ""):
        item.fields[key] = value


def _split_roles(sheets: list[ExtractedSheet]) -> dict[str, list[ExtractedSheet]]:
    buckets: dict[str, list[ExtractedSheet]] = {
        "invoice": [],
        "packing": [],
        "specification": [],
        "catalog": [],
        "goods": [],
        "mixed": [],
    }
    for sheet in sheets:
        buckets.setdefault(sheet.role, []).append(sheet)
    return buckets


def _aggregate_detail(rows: Iterable[Row]) -> dict[str, dict[str, Any]]:
    """Sum per-roll specification rows by match key."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = match_key(row.article)
        if not key:
            continue
        agg = out.setdefault(key, {"article": row.article, "rolls": 0})
        agg["rolls"] += 1
        for f in ("meters", "area", "net_weight", "gross_weight"):
            v = row.fields.get(f)
            if isinstance(v, (int, float)):
                agg[f] = round(agg.get(f, 0) + v, 6)
        for f in ("hs_code", "customs_code", "description", "price", "width", "color"):
            if f not in agg and row.fields.get(f) not in (None, ""):
                agg[f] = row.fields[f]
    return out


def _positive_shares(children: list[CanonicalItem], field_candidates: tuple[str, ...]) -> list[float]:
    """Pick the first candidate field that yields a positive total across children."""
    for field_name in field_candidates:
        weights: list[float] = []
        for child in children:
            value = child.fields.get(field_name)
            weights.append(float(value) if isinstance(value, (int, float)) and value > 0 else 0.0)
        if sum(weights) > 0:
            return weights
    return [1.0] * len(children)


def _assign_total(children: list[CanonicalItem], field_name: str, total: float, shares: list[float]) -> None:
    """Split `total` across children by shares; last child gets the remainder (no drift)."""
    share_sum = sum(shares) or float(len(children))
    assigned = 0.0
    for index, (child, share) in enumerate(zip(children, shares)):
        if index == len(children) - 1:
            child.fields[field_name] = round(total - assigned, 2)
        else:
            part = round(total * (share / share_sum), 2)
            child.fields[field_name] = part
            assigned += part


def merge_documents(
    sheets: list[ExtractedSheet],
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> list[CanonicalItem]:
    catalog = catalog or {}
    buckets = _split_roles(sheets)
    items: dict[str, CanonicalItem] = {}
    lots_of: dict[str, list[str]] = {}

    def _by_article(key: str) -> list[CanonicalItem]:
        return [items[uid] for uid in lots_of.get(key, []) if uid in items]

    def _new_item(article: str, key: str) -> CanonicalItem:
        uid = f"{key}#{len(lots_of.get(key, []))}"
        it = CanonicalItem(article=normalize_text(article), key=key)
        items[uid] = it
        lots_of.setdefault(key, []).append(uid)
        return it

    def item_for(article: str) -> CanonicalItem:
        key = match_key(article)
        existing = _by_article(key)
        if existing:
            return existing[0]
        return _new_item(article, key)

    def item_for_lot(article: str, fields: dict[str, Any]) -> CanonicalItem:
        """Same Art No. + different qty is another lot, not one collapsed row."""
        key = match_key(article)
        token = lot_token(fields)
        existing = _by_article(key)
        if token:
            for it in existing:
                if lot_token(it.fields) == token:
                    return it
            return _new_item(article, key)
        if existing:
            return existing[0]
        return _new_item(article, key)

    def pick_lot(article: str, fields: dict[str, Any]) -> CanonicalItem | None:
        key = match_key(article)
        existing = _by_article(key)
        if not existing:
            return None
        if len(existing) == 1:
            return existing[0]
        token = lot_token(fields)
        if token:
            for it in existing:
                if lot_token(it.fields) == token:
                    return it
                if any(lot_token(line) == token for line in it.lines):
                    return it
            empty = [it for it in existing if it.fields.get("net_weight") in (None, "")]
            if empty:
                return empty[0]
            return None
        if len(existing) == 1:
            return existing[0]
        return existing[0]

    # 1) commercial / goods rows first (they define the article list + price).
    #    A non-detail "specification" sheet (Tosun/Weavers/MORA) IS the per-article
    #    invoice+packing table, so it counts as a goods source too.
    for sheet in buckets["invoice"] + buckets["mixed"] + buckets["goods"] + buckets["specification"]:
        if sheet.detail:
            continue
        for row in sheet.rows:
            if row.is_group or not match_key(row.article):
                continue
            if not any(
                isinstance(row.fields.get(key), (int, float))
                for key in ("qty", "meters", "area", "price", "amount", "net_weight", "gross_weight", "rolls", "boxes")
            ):
                continue
            it = item_for_lot(row.article, row.fields)
            if not it.fields.get("article_display"):
                it.article = row.article
            it.sources.append(row.source)
            if row.lines and not it.lines:
                it.lines = [dict(line) for line in row.lines]
            elif row.lines and it.lines:
                _merge_lines(it, row.lines)
            if row.item_no is not None:
                _prefer(it, "_no", row.item_no)
            if row.fields.get("_group"):
                _prefer(it, "_group", row.fields.get("_group"))
            for k, v in row.fields.items():
                if k.startswith("_") and k not in {"_pack_group", "_group", "_no"}:
                    continue
                _prefer(it, k, v)

    # 2) sender Specification (per-roll detail) -> aggregate by article.
    #    Weights go into `_spec_*` so packing can still be the authority for the export,
    #    while the per-article shape follows the sender detail.
    for sheet in buckets["specification"] + buckets["goods"] + buckets["invoice"] + buckets["mixed"]:
        if not sheet.detail:
            continue
        agg = _aggregate_detail(sheet.rows)
        for key, data in agg.items():
            cands = _by_article(key)
            if not cands:
                cands = [item_for(data["article"])]
            # Several commercial lots of one Art No.: do not dump the spec total onto each.
            apply_weights = len(cands) == 1
            for it in cands:
                it.sources.append(sheet.source + " (spec)")
                for k in ("meters", "area", "width"):
                    _prefer(it, k, data.get(k))
                for k in ("hs_code", "customs_code", "description", "price", "color"):
                    _prefer(it, k, data.get(k))
                if not apply_weights:
                    continue
                for k in ("net_weight", "gross_weight"):
                    value = data.get(k)
                    if isinstance(value, (int, float)):
                        it.fields[f"_spec_{k}"] = value
                        # fallback only; packing (step 3) overwrites when present
                        _prefer(it, k, value)

    # 3) packing rows: direct match by article+qty, else family distribution (PL is authoritative)
    family_rows: list[Row] = []
    for sheet in buckets["packing"]:
        if sheet.detail:
            continue
        for row in sheet.rows:
            article = row.article
            category, sku = split_design(article)
            if sku and _by_article(match_key(sku)):
                article = sku
            key = match_key(article)
            if not key:
                continue
            prefix_children = [
                it
                for it in items.values()
                if key and len(key) >= 3 and it.key.startswith(key) and len(it.key) > len(key)
            ]
            if _by_article(key):
                it = pick_lot(article, row.fields) or item_for_lot(article, row.fields)
                it.sources.append(row.source)
                if category:
                    _prefer(it, "_group", category)
                if row.fields.get("_group"):
                    _prefer(it, "_group", row.fields.get("_group"))
                if row.lines:
                    _merge_lines(it, row.lines)
                else:
                    _apply_packing_to_item(it, row.fields)
                for k in _PACKING_FILL:
                    _prefer(it, k, row.fields.get(k))
                for k in _PACKING_OVERWRITE:
                    if not it.lines:
                        _prefer(it, k, row.fields.get(k), overwrite=True)
                if row.fields.get("_pack_group"):
                    _prefer(it, "_pack_group", row.fields.get("_pack_group"))
            elif _GROUP_PREFIX.match(row.article) or prefix_children:
                family_rows.append(row)
            else:
                it = pick_lot(article, row.fields) or item_for_lot(article, row.fields)
                it.sources.append(row.source)
                if category:
                    _prefer(it, "_group", category)
                if row.lines:
                    _merge_lines(it, row.lines)
                else:
                    _apply_packing_to_item(it, row.fields)
                for k in _PACKING_FILL:
                    _prefer(it, k, row.fields.get(k))
                for k in _PACKING_OVERWRITE:
                    if not it.lines:
                        _prefer(it, k, row.fields.get(k), overwrite=True)
                if row.fields.get("_pack_group"):
                    _prefer(it, "_pack_group", row.fields.get("_pack_group"))

    _distribute_families(family_rows, items)
    _attach_components(sheets, items)
    _flag_weight_drift(items)

    # 4) catalog fill (authoritative for codes + bilingual description)
    by_model = _catalog_by_model(catalog)
    for it in items.values():
        hit = catalog.get(it.key) or by_model.get(it.key)
        if not hit:
            model_key = match_key(str(it.fields.get("model") or ""))
            if model_key:
                hit = catalog.get(model_key) or by_model.get(model_key)
        if hit:
            _prefer(it, "customs_code", hit.get("customs_code"), overwrite=True)
            _prefer(it, "hs_code", hit.get("hs_code"))
            if hit.get("description"):
                it.fields["description"] = hit["description"]
            _prefer(it, "manufacturer", hit.get("manufacturer"))
            _prefer(it, "country", hit.get("country"))
        else:
            if not it.fields.get("customs_code") and not it.fields.get("hs_code"):
                it.flags.append({
                    "severity": "yellow",
                    "field_name": "tnved_code",
                    "error_type": "catalog_not_found",
                    "catalog": "нет строки",
                    "message": f"Нет кода ТН ВЭД для «{it.article}» - заполни вручную или добавь в справочник.",
                })
            if not it.fields.get("description"):
                it.flags.append({
                    "severity": "yellow",
                    "field_name": "description",
                    "error_type": "description_missing",
                    "catalog": "нет строки",
                    "message": (
                        f"Нет наименования для «{it.article}» - в инвойсе его часто нет, "
                        "нужен справочник (сводная/описание) или ручной ввод."
                    ),
                })

    # 5) derive amount / price and sanity flags
    for it in items.values():
        _finalize(it)

    ordered = [it for it in items.values() if it.key]
    return ordered


def _line_snapshot(fields: dict[str, Any] | None) -> dict[str, Any]:
    data = fields or {}
    keep = (
        "qty", "unit", "price", "amount", "color",
        "net_weight", "gross_weight", "volume", "boxes", "rolls",
        "measurement", "pcs_per_carton", "meters", "area", "width",
    )
    return {k: v for k, v in data.items() if k in keep and v not in (None, "")}


def _refresh_item_from_lines(it: CanonicalItem) -> None:
    if not it.lines:
        return
    for key in ("qty", "amount", "meters", "area", "net_weight", "gross_weight", "volume", "boxes", "rolls"):
        vals = [line[key] for line in it.lines if isinstance(line.get(key), (int, float))]
        if vals:
            it.fields[key] = round(sum(vals), 6)


def _merge_lines(it: CanonicalItem, incoming: list[dict[str, Any]]) -> None:
    if not incoming:
        return
    if not it.lines:
        it.lines = [dict(line) for line in incoming]
        _refresh_item_from_lines(it)
        return
    used: set[int] = set()
    item_token = lot_token(it.fields)
    for extra in incoming:
        token = lot_token(extra)
        # Packing-list row restating the whole commercial qty is an item total,
        # not another match for the first sub-line (which often carries the same qty).
        if token and item_token and token == item_token and len(it.lines) > 1:
            continue
        matched = None
        for idx, line in enumerate(it.lines):
            if idx in used:
                continue
            if token and lot_token(line) == token:
                matched = idx
                break
            if not token and not lot_token(line):
                matched = idx
                break
        if matched is None:
            it.lines.append(dict(extra))
            continue
        used.add(matched)
        line = it.lines[matched]
        for key, value in extra.items():
            if value in (None, ""):
                continue
            if key in ("net_weight", "gross_weight", "volume", "boxes", "measurement", "pcs_per_carton"):
                line[key] = value
            elif line.get(key) in (None, ""):
                line[key] = value
    _refresh_item_from_lines(it)


def _apply_packing_to_item(it: CanonicalItem, fields: dict[str, Any]) -> None:
    token = lot_token(fields)
    if it.lines and token and token == lot_token(it.fields):
        for key in _PACKING_FILL + _PACKING_OVERWRITE:
            if fields.get(key) not in (None, ""):
                it.fields[key] = fields[key]
        return
    if it.lines and token:
        for line in it.lines:
            if lot_token(line) == token:
                for key in ("net_weight", "gross_weight", "volume", "boxes", "rolls", "measurement", "pcs_per_carton"):
                    if fields.get(key) not in (None, ""):
                        line[key] = fields[key]
                _refresh_item_from_lines(it)
                return
    if it.lines and not token:
        it.lines.append(_line_snapshot(fields))
        _refresh_item_from_lines(it)


def _parent_key(article: str, known: set[str]) -> str | None:
    """Longest existing goods key that this SKU is a hyphen-suffix component of."""
    text = (article or "").strip()
    if not text:
        return None
    parts = re.split(r"[-_/]", text)
    if len(parts) < 2:
        return None
    for end in range(len(parts) - 1, 0, -1):
        cand = "-".join(parts[:end]).strip()
        key = match_key(cand)
        if key and key in known:
            return key
    return None


def _catalog_by_model(catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index catalog rows by a model field when the article key itself did not hit."""
    out: dict[str, dict[str, Any]] = {}
    for row in catalog.values():
        model = row.get("model")
        key = match_key(str(model or ""))
        if key and key not in out:
            out[key] = row
    return out


def _attach_components(sheets: list[ExtractedSheet], items: dict[str, CanonicalItem]) -> None:
    """Post-TOTAL detail packing belongs to parent articles, never as extra goods."""
    known = {it.key for it in items.values()}
    groups: dict[str, list[str]] = {}
    for it in items.values():
        group = str(it.fields.get("_pack_group") or "")
        if group:
            groups.setdefault(group, []).append(it.key)
    measurements: dict[str, list[str]] = {}
    for sheet in sheets:
        for row in getattr(sheet, "component_rows", None) or []:
            parent = _parent_key(row.article, known)
            if not parent:
                continue
            meas = row.fields.get("measurement")
            if meas:
                measurements.setdefault(parent, [])
                if meas not in measurements[parent]:
                    measurements[parent].append(str(meas))
    for parent, values in measurements.items():
        children = [it for it in items.values() if it.key == parent]
        group_keys: list[str] = []
        for child in children:
            group = str(child.fields.get("_pack_group") or "")
            group_keys.extend(groups.get(group, [child.key]))
        joined = " + ".join(values)
        targets = [it for it in items.values() if it.key in set(group_keys) or it.key == parent]
        if not targets:
            continue
        first = targets[0]
        if first.fields.get("measurement") in (None, ""):
            first.fields["measurement"] = joined
        if first.lines and first.lines[0].get("measurement") in (None, ""):
            first.lines[0]["measurement"] = joined


def _distribute_families(family_rows: list[Row], items: dict[str, CanonicalItem]) -> None:
    for fam in family_rows:
        model = _family_model(fam.article)
        model_key = match_key(model)
        if not model_key:
            continue
        children = [it for it in items.values() if it.key.startswith(model_key)]
        if not children:
            # keep the family itself as an item so its data is not lost
            fam_key = match_key(fam.article)
            existing = [it for it in items.values() if it.key == fam_key]
            it = existing[0] if existing else CanonicalItem(
                article=normalize_text(fam.article), key=fam_key
            )
            if not existing:
                items[f"{fam_key}#0"] = it
            for k in ("net_weight", "gross_weight", "meters", "area", "rolls", "measurement", "gm"):
                if fam.fields.get(k) not in (None, ""):
                    it.fields.setdefault(k, fam.fields[k])
            continue

        # Net: prefer sender-spec net shares so colour lines match the ready etalon.
        # Gross: prefer sender-spec gross shares (not meters - that skewed G.W vs etalon).
        net = fam.fields.get("net_weight")
        gross = fam.fields.get("gross_weight")
        if isinstance(net, (int, float)):
            _assign_total(
                children,
                "net_weight",
                float(net),
                _positive_shares(children, ("_spec_net_weight", "meters", "area", "qty")),
            )
        if isinstance(gross, (int, float)):
            _assign_total(
                children,
                "gross_weight",
                float(gross),
                _positive_shares(children, ("_spec_gross_weight", "_spec_net_weight", "meters", "area", "qty")),
            )
        for k in ("gm", "measurement"):
            if fam.fields.get(k) not in (None, ""):
                for child in children:
                    child.fields.setdefault(k, fam.fields[k])

        # Family-level compare: sum of sender-spec vs packing total.
        for field_name, pl_value in (("net_weight", net), ("gross_weight", gross)):
            if not isinstance(pl_value, (int, float)):
                continue
            spec_sum = sum(
                float(child.fields[f"_spec_{field_name}"])
                for child in children
                if isinstance(child.fields.get(f"_spec_{field_name}"), (int, float))
            )
            if spec_sum <= 0:
                continue
            drift = abs(spec_sum - float(pl_value))
            if drift <= WEIGHT_TOLERANCE_KG:
                continue
            label = "нетто" if field_name == "net_weight" else "брутто"
            # one flag on the first child is enough - avoids N identical warnings
            children[0].flags.append({
                "severity": "yellow",
                "field_name": field_name,
                "error_type": "weight_pl_vs_spec",
                "message": (
                    f"«{model}»: {label} в пакинге {pl_value} кг, сумма по спецификации "
                    f"отправителя {round(spec_sum, 2)} кг (разница {round(drift, 2)} кг > "
                    f"{WEIGHT_TOLERANCE_KG} кг). В готовой спец взят вес из пакинга."
                ),
            })


def _flag_weight_drift(items: dict[str, CanonicalItem]) -> None:
    """Per-article PL vs sender-spec compare for direct (non-family) packing hits."""
    for it in items.values():
        for field_name in ("net_weight", "gross_weight"):
            final = it.fields.get(field_name)
            spec = it.fields.get(f"_spec_{field_name}")
            if not isinstance(final, (int, float)) or not isinstance(spec, (int, float)):
                continue
            drift = abs(float(final) - float(spec))
            if drift <= WEIGHT_TOLERANCE_KG:
                continue
            if any(
                f.get("error_type") == "weight_pl_vs_spec" and f.get("field_name") == field_name
                for f in it.flags
            ):
                continue
            label = "нетто" if field_name == "net_weight" else "брутто"
            it.flags.append({
                "severity": "yellow",
                "field_name": field_name,
                "error_type": "weight_pl_vs_spec",
                "message": (
                    f"«{it.article}»: {label} пакинг {final} кг, спецификация отправителя "
                    f"{spec} кг (разница {round(drift, 2)} кг > {WEIGHT_TOLERANCE_KG} кг). "
                    "В готовой спец взят вес из пакинга."
                ),
            })


def _finalize(it: CanonicalItem) -> None:
    f = it.fields
    price = f.get("price")
    amount = f.get("amount")
    basis = None
    for k in ("meters", "qty", "area"):
        v = f.get(k)
        if isinstance(v, (int, float)) and v:
            basis = float(v)
            break
    if amount is None and isinstance(price, (int, float)) and basis is not None:
        f["amount"] = round(price * basis, 2)
    elif price is None and isinstance(amount, (int, float)) and basis:
        f["price"] = round(amount / basis, 4)
    elif isinstance(price, (int, float)) and isinstance(amount, (int, float)) and basis:
        expected = price * basis
        if expected and abs(expected - amount) / max(abs(amount), 1) > 0.03:
            it.flags.append({
                "severity": "red",
                "field_name": "amount",
                "error_type": "mismatch",
                "message": (
                    f"«{it.article}»: сумма {amount} не сходится с ценой x количество "
                    f"({round(expected, 2)}). Проверь исходник."
                ),
            })
    # de-dup sources
    seen: set[str] = set()
    it.sources = [s for s in it.sources if not (s in seen or seen.add(s))]
