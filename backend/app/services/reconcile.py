"""Core reconciliation & validation — Word spec §§2–4, §7.

Rules enforced here:
- Match positions ONLY by normalized article/model (no semantic merge).
- Never silently pick a value when documents disagree — emit MISMATCH.
- Catalog fills TN VED / bilingual desc only on exact article hit.
- Packing aggregates may be distributed to detail rows by meters/area share.
- Rows without article are flagged, never attached to a neighbour.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import DocType, ErrorSeverity, ErrorType
from app.parsing.table_rows import is_usable_row
from app.parsing.normalize import normalize_article
from app.parsing.schemas import ParsedDocument, ParsedLine
from app.services.catalog import CatalogIndex
from app.services.field_map import is_factory_note, map_row, repair_line_amount

COMPARE_FIELDS = (
    ("qty", "commercial"),
    ("price", "commercial"),
    ("amount", "commercial"),
    ("rolls", "packing"),
    ("boxes", "packing"),
    ("meters", "packing"),
    ("area", "packing"),
    ("net_weight", "packing"),
    ("gross_weight", "packing"),
    ("volume", "packing"),
    ("hs_code", "customs"),
    ("tnved_code", "customs"),
)

NUMERIC_TOLERANCE = 1e-6


@dataclass
class ValidationFlag:
    field_name: str
    error_type: ErrorType
    severity: ErrorSeverity
    details: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class ReconciledItem:
    article: str | None
    model: str | None
    normalized_article: str
    commercial_data: dict[str, Any] = field(default_factory=dict)
    packing_data: dict[str, Any] = field(default_factory=dict)
    customs_data: dict[str, Any] = field(default_factory=dict)
    source_traces: dict[str, Any] = field(default_factory=dict)
    flags: list[ValidationFlag] = field(default_factory=list)
    invoice_subkit: str | None = None


def _bucket_key(doc_type: DocType | None) -> str | None:
    if doc_type is None:
        return None
    return {
        DocType.INVOICE: "invoice",
        DocType.PACKING_LIST: "packing_list",
        DocType.SPECIFICATION: "specification",
        DocType.CATALOG: "catalog",
        DocType.PERMIT: "permit",
    }.get(doc_type)


def _nearly_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return abs(float(a) - float(b)) <= NUMERIC_TOLERANCE
    except (TypeError, ValueError):
        return str(a).strip().upper() == str(b).strip().upper()


def _qty_token(mapped: dict[str, Any]) -> str:
    qty = mapped.get("qty")
    if qty is None:
        qty = mapped.get("meters")
    try:
        if qty in (None, ""):
            return ""
        return f"{round(float(qty), 6):g}"
    except (TypeError, ValueError):
        return str(qty)


def _lot_store_key(article_key: str, mapped: dict[str, Any]) -> str:
    token = _qty_token(mapped)
    return f"{article_key}#{token}" if token else article_key


def _set_if_empty(target: dict[str, Any], key: str, value: Any) -> None:
    if value is None or value == "":
        return
    if target.get(key) in (None, "", []):
        target[key] = value


def _apply_mapped(
    item: ReconciledItem,
    mapped: dict[str, Any],
    *,
    source: str,
) -> None:
    for key in ("qty", "unit", "price", "amount", "currency", "color"):
        if key in mapped:
            _set_if_empty(item.commercial_data, key, mapped[key])
    for key in (
        "rolls",
        "boxes",
        "meters",
        "area",
        "width",
        "net_weight",
        "gross_weight",
        "volume",
        "measurement",
        "pcs_per_carton",
    ):
        if key in mapped:
            _set_if_empty(item.packing_data, key, mapped[key])
    for key in (
        "hs_code",
        "tnved_code",
        "description_en",
        "description_ru",
        "description",
        "country",
        "manufacturer",
        "brand",
    ):
        if key in mapped:
            if key.startswith("description") and is_factory_note(mapped[key]):
                continue
            _set_if_empty(item.customs_data, key, mapped[key])

    probe = dict(item.commercial_data)
    probe["area"] = item.packing_data.get("area")
    probe["meters"] = item.packing_data.get("meters")
    repair_line_amount(probe)
    if probe.get("amount") not in (None, ""):
        item.commercial_data["amount"] = probe["amount"]

    traces = item.source_traces.setdefault(source, {})
    for key, value in mapped.items():
        if value is None or value == "":
            continue
        if key in traces and not _nearly_equal(traces[key], value):
            # Keep both sides in traces; mismatch flagged later
            traces[f"{key}__alt"] = value
        else:
            traces[key] = value


def _detect_subkit(filename: str, text: str = "") -> str | None:
    import re

    blob = f"{filename} {text}"
    match = re.search(r"\b(\d{2,4}-\d)\b", blob)
    return match.group(1) if match else None


def _flag_mismatches(item: ReconciledItem) -> None:
    sources = [s for s in ("invoice", "packing_list", "specification") if s in item.source_traces]
    for field_name, _group in COMPARE_FIELDS:
        values: dict[str, Any] = {}
        for source in sources:
            raw = item.source_traces[source]
            if field_name in raw and raw[field_name] not in (None, ""):
                values[source] = raw[field_name]
        if len(values) < 2:
            continue
        unique: list[Any] = []
        for value in values.values():
            if not any(_nearly_equal(value, existing) for existing in unique):
                unique.append(value)
        if len(unique) > 1:
            item.flags.append(
                ValidationFlag(
                    field_name=field_name,
                    error_type=ErrorType.MISMATCH,
                    severity=ErrorSeverity.RED,
                    details={"values_by_source": values},
                    message=f"Расхождение по полю «{field_name}» между документами",
                )
            )


def _flag_missing_pairs(item: ReconciledItem, present_doc_types: set[str]) -> None:
    traces = item.source_traces
    if "invoice" in present_doc_types and "invoice" not in traces:
        item.flags.append(
            ValidationFlag(
                field_name="article",
                error_type=ErrorType.MISSING_PAIR,
                severity=ErrorSeverity.YELLOW,
                details={"missing_in": "invoice"},
                message="Позиция не найдена в инвойсе",
            )
        )
    if "packing_list" in present_doc_types and "packing_list" not in traces:
        item.flags.append(
            ValidationFlag(
                field_name="article",
                error_type=ErrorType.MISSING_PAIR,
                severity=ErrorSeverity.YELLOW,
                details={"missing_in": "packing_list"},
                message="Позиция не найдена в упаковочном листе",
            )
        )


def distribute_packing_aggregates(items: list[ReconciledItem]) -> None:
    """De-aggregate PL group totals onto invoice/spec detail rows by meters (fallback: area).

    Word spec §3.4 Noble 110: PL row «Noble» aggregates Noble 110 + Noble 624;
    finished file splits net/gross by model using detail metrics.
    """
    # Group items that share a packing_list parent key prefix when PL is aggregated
    aggregated = [
        item
        for item in items
        if item.packing_data.get("is_aggregated")
        or (
            "packing_list" in item.source_traces
            and item.source_traces["packing_list"].get("is_aggregated")
        )
    ]
    if not aggregated:
        # Heuristic: if one PL key prefixes several invoice keys, distribute
        pl_items = [i for i in items if "packing_list" in i.source_traces]
        inv_items = [i for i in items if "invoice" in i.source_traces]
        for pl in pl_items:
            pl_key = pl.normalized_article
            children = [
                inv
                for inv in inv_items
                if inv.normalized_article != pl_key
                and inv.normalized_article.startswith(pl_key)
                and "packing_list" not in inv.source_traces
            ]
            if len(children) < 2:
                continue
            _distribute_weights(pl, children)


def _distribute_weights(parent: ReconciledItem, children: list[ReconciledItem]) -> None:
    net = parent.packing_data.get("net_weight") or parent.source_traces.get("packing_list", {}).get(
        "net_weight"
    )
    gross = parent.packing_data.get("gross_weight") or parent.source_traces.get(
        "packing_list", {}
    ).get("gross_weight")
    if net is None and gross is None:
        return

    weights: list[float] = []
    for child in children:
        meters = child.packing_data.get("meters") or child.commercial_data.get("qty")
        area = child.packing_data.get("area")
        basis = meters if meters not in (None, 0) else area
        weights.append(float(basis or 0))
    total = sum(weights)
    if total <= 0:
        for child in children:
            child.flags.append(
                ValidationFlag(
                    field_name="net_weight",
                    error_type=ErrorType.MISSING_PAIR,
                    severity=ErrorSeverity.YELLOW,
                    details={"parent_article": parent.normalized_article},
                    message="Не удалось разложить агрегат упаковки: нет метров/площади",
                )
            )
        return

    for child, w in zip(children, weights, strict=True):
        share = w / total
        if net is not None:
            child.packing_data["net_weight"] = round(float(net) * share, 2)
        if gross is not None:
            child.packing_data["gross_weight"] = round(float(gross) * share, 2)
        child.packing_data["distributed_from_article"] = parent.normalized_article
        # Copy rolls/meters from invoice if missing
        pl = parent.source_traces.get("packing_list", {})
        child.source_traces.setdefault("packing_list_distributed", {}).update(
            {
                "parent": parent.normalized_article,
                "share": share,
                "parent_net": net,
                "parent_gross": gross,
                "parent_raw": pl,
            }
        )


def apply_catalog(items: list[ReconciledItem], catalog: CatalogIndex | None) -> None:
    if catalog is None:
        return
    for item in items:
        if not item.normalized_article:
            continue
        hit = catalog.lookup(item.normalized_article)
        if hit is None:
            item.flags.append(
                ValidationFlag(
                    field_name="tnved_code",
                    error_type=ErrorType.CATALOG_NOT_FOUND,
                    severity=ErrorSeverity.YELLOW,
                    details={"normalized_article": item.normalized_article},
                    message="Артикул не найден в справочнике",
                )
            )
            continue
        item.source_traces["catalog"] = {
            "tnved_code": hit.get("tnved_code"),
            "description_en": hit.get("description_en"),
            "description_ru": hit.get("description_ru"),
        }
        # Fill only missing — never silently overwrite invoice HS with catalog TN VED
        _set_if_empty(item.customs_data, "tnved_code", hit.get("tnved_code"))
        _set_if_empty(item.customs_data, "description_en", hit.get("description_en"))
        _set_if_empty(item.customs_data, "description_ru", hit.get("description_ru"))

        inv_hs = item.source_traces.get("invoice", {}).get("hs_code")
        catalog_code = hit.get("tnved_code")
        if inv_hs and catalog_code and not _nearly_equal(inv_hs, catalog_code):
            item.flags.append(
                ValidationFlag(
                    field_name="tnved_code",
                    error_type=ErrorType.MISMATCH,
                    severity=ErrorSeverity.RED,
                    details={"invoice_hs": inv_hs, "catalog_tnved": catalog_code},
                    message="HS code инвойса не совпадает с кодом ТН ВЭД из справочника",
                )
            )


def _drop_prefix_duplicates(items: list[ReconciledItem]) -> list[ReconciledItem]:
    """If both ZIMMY and ZIMMY 162 exist, keep the detailed codes.

    Keep a short packing-only parent (Noble) so weights can still be split.
    """
    by_key = {item.normalized_article: item for item in items}
    keys = list(by_key)
    drop: set[str] = set()
    for key in keys:
        children = [
            other
            for other in keys
            if other != key and other.startswith(key) and other[len(key) :].isdigit()
        ]
        if not children:
            continue
        parent = by_key[key]
        if "packing_list" in parent.source_traces and "invoice" not in parent.source_traces:
            continue
        drop.add(key)
    if not drop:
        return items
    return [item for item in items if item.normalized_article not in drop]


def reconcile_documents(
    documents: list[ParsedDocument],
    *,
    catalog: CatalogIndex | None = None,
) -> list[ReconciledItem]:
    items_by_key: dict[str, ReconciledItem] = {}
    orphan_flags: list[ReconciledItem] = []  # unused; junk rows are dropped earlier
    present_buckets: set[str] = set()

    for doc in documents:
        bucket = _bucket_key(doc.doc_type)
        if bucket:
            present_buckets.add(bucket)
        if bucket == "catalog":
            continue

        subkit = _detect_subkit(doc.filename, doc.text_preview[:500])
        low_ocr = (
            doc.ocr_used
            and doc.ocr_confidence is not None
            and doc.ocr_confidence < 0.75
        )

        for line in doc.lines:
            mapped = map_row(line.raw)
            key = line.normalized_article or normalize_article(
                mapped.get("article") or mapped.get("model") or line.article or line.model
            )
            if not key:
                continue
            if not is_usable_row(line.article or mapped.get("article") or key, mapped):
                continue

            lot_key = _lot_store_key(key, mapped)
            item = items_by_key.get(lot_key)
            if item is None:
                token = _qty_token(mapped)
                siblings = [stored for stored in items_by_key if stored == key or stored.startswith(f"{key}#")]
                if not token and len(siblings) == 1:
                    item = items_by_key[siblings[0]]
            if item is None:
                item = ReconciledItem(
                    article=line.article or mapped.get("article"),
                    model=line.model or mapped.get("model"),
                    normalized_article=key,
                    invoice_subkit=subkit,
                )
                items_by_key[lot_key] = item
            elif item.invoice_subkit is None and subkit:
                item.invoice_subkit = subkit

            _apply_mapped(item, mapped, source=bucket or "invoice")
            if subkit:
                item.commercial_data["invoice_subkit"] = subkit
            if low_ocr:
                item.flags.append(
                    ValidationFlag(
                        field_name="ocr",
                        error_type=ErrorType.LOW_OCR_CONFIDENCE,
                        severity=ErrorSeverity.ORANGE,
                        details={
                            "filename": doc.filename,
                            "ocr_confidence": doc.ocr_confidence,
                        },
                        message="Низкая уверенность распознавания",
                    )
                )
            if line.ocr_confidence is not None and line.ocr_confidence < 0.75:
                item.flags.append(
                    ValidationFlag(
                        field_name="ocr",
                        error_type=ErrorType.LOW_OCR_CONFIDENCE,
                        severity=ErrorSeverity.ORANGE,
                        details={"row_index": line.row_index, "ocr_confidence": line.ocr_confidence},
                        message="Низкая уверенность распознавания по строке",
                    )
                )

    items = list(items_by_key.values())
    items = _drop_prefix_duplicates(items)
    distribute_packing_aggregates(items)
    apply_catalog(items, catalog)

    for item in items:
        _flag_mismatches(item)
        _flag_missing_pairs(item, present_buckets)
        # Deduplicate identical flags
        seen: set[tuple[Any, ...]] = set()
        unique_flags: list[ValidationFlag] = []
        for flag in item.flags:
            sig = (flag.field_name, flag.error_type, flag.severity, flag.message)
            if sig in seen:
                continue
            seen.add(sig)
            unique_flags.append(flag)
        item.flags = unique_flags

    return items


def items_to_dicts(items: list[ReconciledItem]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        result.append(
            {
                "article": item.article,
                "model": item.model,
                "normalized_article": item.normalized_article,
                "commercial_data": deepcopy(item.commercial_data),
                "packing_data": deepcopy(item.packing_data),
                "customs_data": deepcopy(item.customs_data),
                "source_traces": deepcopy(item.source_traces),
                "invoice_subkit": item.invoice_subkit,
                "validation_errors": [
                    {
                        "field_name": f.field_name,
                        "error_type": f.error_type.value,
                        "severity": f.severity.value,
                        "details": f.details,
                        "message": f.message,
                    }
                    for f in item.flags
                ],
            }
        )
    return result
