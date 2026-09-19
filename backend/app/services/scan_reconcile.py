"""Attach PDF-scan review to the Excel-built item table without overwriting it."""

from __future__ import annotations

import uuid
from typing import Any

from app.models.enums import ErrorSeverity, ErrorType
from app.parsing.normalize import normalize_article
from app.parsing.product_row import is_plausible_article
from app.schemas.api import ItemOut, ScanItemOut, ScanTotalsOut, ValidationErrorOut
from app.services.field_map import is_factory_note, nearly_equal, repair_line_amount

COMPARE_FIELDS = (
    ("rolls", "rolls"),
    ("boxes", "boxes"),
    ("price", "price"),
    ("amount", "amount"),
    ("net_weight", "net_weight"),
    ("gross_weight", "gross_weight"),
    ("area", "area"),
    ("volume", "volume"),
)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nearly_equal(left: Any, right: Any, *, tol: float = 0.05) -> bool:
    return nearly_equal(left, right, tol=tol)


def _first_num(*values: Any) -> float | None:
    for value in values:
        number = _num(value)
        if number is not None:
            return number
    return None


def excel_field(item: ItemOut, field: str) -> Any:
    commercial = item.commercial_data or {}
    packing = item.packing_data or {}
    if field == "qty":
        return commercial.get("qty")
    if field == "price":
        return commercial.get("price")
    if field == "amount":
        return commercial.get("amount")
    return packing.get(field)


def compute_excel_totals(items: list[ItemOut]) -> ScanTotalsOut:
    def _sum(field: str) -> float | None:
        values = [_num(excel_field(item, field)) for item in items]
        present = [value for value in values if value is not None]
        if not present:
            return None
        return round(sum(present), 4)

    return ScanTotalsOut(
        qty=_sum("qty"),
        meters=_sum("meters"),
        amount=_sum("amount"),
        rolls=_sum("rolls"),
        boxes=_sum("boxes"),
        net_weight=_sum("net_weight"),
        gross_weight=_sum("gross_weight"),
        area=_sum("area"),
        volume=_sum("volume"),
    )


def _scan_values(hit: ScanItemOut) -> dict[str, Any]:
    return {
        "qty": hit.qty,
        "meters": hit.meters,
        "rolls": hit.rolls,
        "boxes": hit.boxes,
        "price": hit.price,
        "amount": hit.amount,
        "net_weight": hit.net_weight,
        "gross_weight": hit.gross_weight,
        "area": hit.area,
        "volume": hit.volume,
        "unit": hit.unit,
    }


def _as_hit(row: Any) -> ScanItemOut:
    if isinstance(row, ScanItemOut):
        return row
    return ScanItemOut.model_validate(row)


def _set_item_number(item: ItemOut, field: str, value: float) -> None:
    if field in {"price", "amount", "qty"}:
        item.commercial_data[field] = value
        return
    item.packing_data[field] = value


def _plausible_excel_amount(item: ItemOut, amount: float) -> bool:
    commercial = item.commercial_data or {}
    packing = item.packing_data or {}
    price = _num(commercial.get("price"))
    qty = _num(commercial.get("qty"))
    if qty is None:
        qty = _num(packing.get("meters"))
    area = _num(packing.get("area"))
    if area is not None and nearly_equal(amount, area) and price and qty:
        expected = float(price) * float(qty)
        if not nearly_equal(amount, expected, tol=max(0.05, abs(expected) * 0.02)):
            return False
    if price and qty:
        expected = float(price) * float(qty)
        if nearly_equal(amount, expected, tol=max(0.05, abs(expected) * 0.02)):
            return True
        parser_amount = _num(commercial.get("amount"))
        if parser_amount is not None and area is not None and nearly_equal(parser_amount, area):
            return True
        return False
    return True


def _item_qty(item: ItemOut) -> float | None:
    commercial = item.commercial_data or {}
    packing = item.packing_data or {}
    qty = _num(commercial.get("qty"))
    if qty is not None:
        return qty
    return _num(packing.get("meters"))


def _match_item(items: list[ItemOut], hit: ScanItemOut) -> ItemOut | None:
    key = normalize_article(hit.article or hit.matched_article)
    if not key:
        return None
    candidates = [
        item
        for item in items
        if normalize_article(item.normalized_article or item.article or item.model) == key
    ]
    if not candidates:
        return None
    hit_qty = _num(hit.qty)
    if hit_qty is not None:
        for item in candidates:
            if _nearly_equal(_item_qty(item), hit_qty):
                return item
        return None
    return candidates[0]


def _apply_excel_sourced_numbers(items: list[ItemOut], hits: list[ScanItemOut]) -> None:
    for hit in hits:
        item = _match_item(items, hit)
        if item is None:
            continue
        notes = str(hit.notes or "").lower()
        excel_hint = notes.startswith("excel:") or "excel" in notes
        if hit.verdict not in {"question", "ok"} and not excel_hint:
            continue
        scan_vals = _scan_values(hit)
        for field in ("price", "amount", "qty", "meters", "rolls", "boxes", "net_weight", "gross_weight", "volume"):
            model_val = _num(scan_vals.get(field))
            if model_val is None:
                continue
            current = _num(excel_field(item, field))
            if current is not None and nearly_equal(model_val, current):
                continue
            if field == "amount" and not _plausible_excel_amount(item, model_val):
                continue
            if field == "qty":
                area = _num((item.packing_data or {}).get("area"))
                if area is not None and nearly_equal(model_val, area):
                    continue
            _set_item_number(item, field, model_val)
            item.validation_errors.append(
                ValidationErrorOut(
                    id=uuid.uuid4(),
                    field_name=field,
                    error_type=ErrorType.MODEL_CORRECTION,
                    severity=ErrorSeverity.YELLOW,
                    details={"was": current, "excel": model_val},
                    message=f"Модель подставила «{field}» из Excel: было {current}, стало {model_val}",
                    resolved=True,
                )
            )
        if hit.description and not is_factory_note(hit.description):
            if not (
                (item.customs_data or {}).get("description")
                or (item.customs_data or {}).get("description_en")
                or (item.customs_data or {}).get("description_ru")
            ):
                item.customs_data = {**(item.customs_data or {}), "description": hit.description}
        probe = dict(item.commercial_data)
        probe["area"] = (item.packing_data or {}).get("area")
        probe["meters"] = (item.packing_data or {}).get("meters")
        repair_line_amount(probe)
        if probe.get("amount") not in (None, ""):
            item.commercial_data["amount"] = probe["amount"]


def _item_from_scan_hit(hit: ScanItemOut) -> ItemOut:
    article = (hit.article or hit.matched_article or "").strip()
    commercial: dict[str, Any] = {}
    packing: dict[str, Any] = {}
    if hit.qty is not None:
        commercial["qty"] = hit.qty
    if hit.unit:
        commercial["unit"] = hit.unit
    if hit.price is not None:
        commercial["price"] = hit.price
    if hit.amount is not None:
        commercial["amount"] = hit.amount
    if hit.color:
        commercial["color"] = hit.color
    for field in ("meters", "rolls", "net_weight", "gross_weight", "area", "boxes", "volume"):
        value = getattr(hit, field, None)
        if value is not None:
            packing[field] = value
    customs = {}
    if hit.description and not is_factory_note(hit.description):
        customs["description"] = hit.description
    return ItemOut(
        id=uuid.uuid4(),
        article=article,
        model=article,
        normalized_article=normalize_article(article),
        commercial_data=commercial,
        packing_data=packing,
        customs_data=customs,
        source_traces={"invoice": {"article": article, "source": "excel_model"}},
        validation_errors=[
            ValidationErrorOut(
                id=uuid.uuid4(),
                field_name="article",
                error_type=ErrorType.MODEL_CORRECTION,
                severity=ErrorSeverity.YELLOW,
                details={"excel": article},
                message=f"Модель добавила позицию «{article}» из Excel",
                resolved=True,
            )
        ],
    )


def _append_excel_sourced_items(items: list[ItemOut], hits: list[ScanItemOut]) -> None:
    known = {
        normalize_article(item.normalized_article or item.article or item.model)
        for item in items
        if item.normalized_article or item.article or item.model
    }
    known_lots: set[tuple[str, str]] = set()
    for item in items:
        key = normalize_article(item.normalized_article or item.article or item.model)
        qty = _item_qty(item)
        if key and qty is not None:
            known_lots.add((key, f"{round(float(qty), 6):g}"))
    for hit in hits:
        if hit.verdict != "extra":
            continue
        article = (hit.article or hit.matched_article or "").strip()
        key = normalize_article(article)
        if not key:
            continue
        if not is_plausible_article(article):
            continue
        if hit.qty is None and hit.amount is None and hit.price is None:
            continue
        qty = _num(hit.qty)
        lot_id = (key, f"{round(float(qty), 6):g}") if qty is not None else None
        if lot_id and lot_id in known_lots:
            continue
        if key in known and qty is None:
            continue
        items.append(_item_from_scan_hit(hit))
        known.add(key)
        if lot_id:
            known_lots.add(lot_id)


def apply_scan_review(items: list[ItemOut], review: dict[str, Any]) -> dict[str, Any]:
    """Flag Excel rows from scan hits. Never copy PDF numbers into the working row."""
    review = dict(review)
    excel_totals = compute_excel_totals(items)
    review["excel_totals"] = excel_totals.model_dump()
    hits = [_as_hit(row) for row in (review.get("items") or [])]
    if review.get("excel_attached"):
        _apply_excel_sourced_numbers(items, hits)
        _append_excel_sourced_items(items, hits)
        excel_totals = compute_excel_totals(items)
        review["excel_totals"] = excel_totals.model_dump()
    used_ids: set[str] = set()
    by_key: dict[str, ItemOut] = {}
    for item in items:
        key = item.normalized_article or normalize_article(item.article or item.model)
        if key and key not in by_key:
            by_key[key] = item

    attached: list[dict[str, Any]] = []
    for hit in hits:
        key = normalize_article(hit.article or hit.matched_article)
        item = by_key.get(key) if key else None
        if item is None:
            dump = hit.model_dump()
            dump["verdict"] = "extra"
            dump["item_id"] = None
            attached.append(dump)
            continue
        used_ids.add(str(item.id))
        dump = hit.model_dump()
        dump["item_id"] = str(item.id)
        dump["matched_article"] = item.article
        scan_vals = _scan_values(hit)
        mismatches: list[str] = []
        if hit.description and not (
            (item.customs_data or {}).get("description")
            or (item.customs_data or {}).get("description_en")
            or (item.customs_data or {}).get("description_ru")
        ):
            blob = str(item.source_traces or {}).lower()
            proposed = str(hit.description).strip()
            if proposed and proposed.lower() in blob:
                item.customs_data = {**(item.customs_data or {}), "description": proposed}
        scan_qty = _first_num(scan_vals.get("qty"), scan_vals.get("meters"))
        excel_qty = _first_num(excel_field(item, "qty"), excel_field(item, "meters"))
        qty_field = "qty" if excel_field(item, "qty") not in (None, "") else "meters"
        if scan_qty is not None and excel_qty is not None and not _nearly_equal(scan_qty, excel_qty):
            mismatches.append(qty_field)
            item.validation_errors.append(
                ValidationErrorOut(
                    id=uuid.uuid4(),
                    field_name=qty_field,
                    error_type=ErrorType.SCAN_MISMATCH,
                    severity=ErrorSeverity.RED,
                    details={"excel": excel_qty, "scan": scan_qty, "source": "pdf"},
                    message=f"Скан: {qty_field}={scan_qty}, Excel={excel_qty}",
                    resolved=False,
                )
            )
        for scan_field, excel_name in COMPARE_FIELDS:
            scan_val = scan_vals.get(scan_field)
            excel_val = excel_field(item, excel_name)
            if scan_val in (None, "") or excel_val in (None, ""):
                continue
            if _nearly_equal(scan_val, excel_val):
                continue
            mismatches.append(scan_field)
            item.validation_errors.append(
                ValidationErrorOut(
                    id=uuid.uuid4(),
                    field_name=excel_name,
                    error_type=ErrorType.SCAN_MISMATCH,
                    severity=ErrorSeverity.RED,
                    details={"excel": excel_val, "scan": scan_val, "source": "pdf"},
                    message=f"Скан: {scan_field}={scan_val}, Excel={excel_val}",
                    resolved=False,
                )
            )
        if mismatches:
            dump["verdict"] = "question"
            if not dump.get("notes"):
                dump["notes"] = "не совпало: " + ", ".join(mismatches)
        elif dump.get("verdict") not in {"extra", "missing"}:
            dump["verdict"] = "ok"
        attached.append(dump)

    if used_ids:
        for item in items:
            if str(item.id) in used_ids:
                continue
            if not (item.normalized_article or item.article):
                continue
            attached.append(
                {
                    "article": item.article,
                    "matched_article": item.article,
                    "item_id": str(item.id),
                    "verdict": "missing",
                    "notes": "нет пары на скане",
                }
            )
            item.validation_errors.append(
                ValidationErrorOut(
                    id=uuid.uuid4(),
                    field_name="article",
                    error_type=ErrorType.MISSING_PAIR,
                    severity=ErrorSeverity.YELLOW,
                    details={"missing_in": "scan"},
                    message="Позиция не найдена на скане",
                    resolved=False,
                )
            )

    pdf_totals = review.get("totals") or {}
    if isinstance(pdf_totals, dict):
        pdf_qty = _first_num(pdf_totals.get("qty"), pdf_totals.get("meters"))
        excel_qty = _first_num(excel_totals.qty, excel_totals.meters)
        if pdf_qty is not None and excel_qty is not None and not _nearly_equal(pdf_qty, excel_qty, tol=0.5):
            review["totals_mismatch"] = True
        for field in ("amount", "rolls", "boxes", "net_weight", "gross_weight", "area", "volume"):
            left = _num(pdf_totals.get(field))
            right = _num(getattr(excel_totals, field))
            if left is None or right is None:
                continue
            if not _nearly_equal(left, right, tol=0.5):
                review["totals_mismatch"] = True
                break

    review["items"] = attached
    return review
