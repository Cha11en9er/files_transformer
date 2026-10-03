"""Разбор поставки новым чтением и одним вердиктом модели.

Фото в архив не пишутся. Второй прогон исследования и запись «почему не разобрал» сюда не входят.
Справочник и прочие файлы из дополнительного слота в лоты не попадают: по артикулу дописываются только пустые код и описание.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.parsing.header_extract import is_catalog_filename
from app.parsing.normalize import normalize_article
from app.services.catalog import CatalogIndex

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from prepare_transform_code.shipment import analyze
from prepare_transform_code.verdict import build_prompt

THIN_COLUMN_LIMIT = 4
_EXCEL = {".xlsx", ".xls", ".xlsm"}


def model_spec(slot: int) -> str:
    """1 — OPENCODE_MODEL_1, иначе прежний OPENCODE_MODEL. 2 — только OPENCODE_MODEL_2."""
    if slot == 1:
        return (os.getenv("OPENCODE_MODEL_1") or os.getenv("OPENCODE_MODEL") or "").strip()
    return (os.getenv("OPENCODE_MODEL_2") or "").strip()


def is_reference_name(filename: str, catalog_names: set[str] | None) -> bool:
    name = Path(str(filename or "").replace("\\", "/")).name
    if name.lower() in {item.lower() for item in (catalog_names or set())}:
        return True
    return is_catalog_filename(name)


def split_uploads(
    saved: list[tuple[str, str]],
    catalog_names: set[str] | None,
) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    goods: list[tuple[Path, str]] = []
    references: list[tuple[Path, str]] = []
    for raw, display in saved:
        path = Path(raw)
        if is_reference_name(display, catalog_names):
            references.append((path, display))
        else:
            goods.append((path, display))
    return goods, references


def read_goods(goods: list[tuple[Path, str]]) -> dict[str, Any]:
    """analyze видит только файлы поставки, не справочник из той же загрузки."""
    if not goods:
        return {
            "documents": [],
            "lots": [],
            "flags": [],
            "columns": [],
            "currency": "",
            "currencies": [],
        }
    with tempfile.TemporaryDirectory(prefix="goods_") as folder:
        dest = Path(folder)
        for path, display in goods:
            target = dest / Path(display).name
            if target.exists():
                target = dest / f"{path.stem}_{path.suffix}"
            shutil.copy(path, target)
        return analyze(dest)


def verdict_prompt(draft: dict[str, Any]) -> str:
    return build_prompt(draft)


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _row_cells(row: dict[str, Any]) -> list[Any]:
    """Те же клетки, что в таблице сайта. Прочерк вместо артикула — пустая клетка."""
    commercial = row.get("commercial_data") or {}
    packing = row.get("packing_data") or {}
    customs = row.get("customs_data") or {}
    article = row.get("article")
    if article in {None, "", "-"}:
        article = None
    return [
        article,
        packing.get("rolls"),
        packing.get("meters") if _filled(packing.get("meters")) else commercial.get("qty"),
        packing.get("width"),
        packing.get("area"),
        packing.get("net_weight"),
        packing.get("gross_weight"),
        commercial.get("price"),
        commercial.get("amount"),
        customs.get("hs_code"),
        customs.get("tnved_code"),
        customs.get("description") or customs.get("description_ru") or customs.get("description_en"),
    ]


def filled_base_columns(lots: list[dict[str, Any]]) -> int:
    """Столбец заполнен, если значение есть хотя бы в одной строке. Пустой целиком не считается."""
    rows = lots_to_rows([lot for lot in lots if not lot.get("freight")], [])
    if not rows:
        return 0
    count = 0
    width = len(_row_cells(rows[0]))
    for index in range(width):
        if any(_filled(_row_cells(row)[index]) for row in rows):
            count += 1
    return count


def needs_second_model(lots: list[dict[str, Any]]) -> bool:
    return filled_base_columns(lots) <= THIN_COLUMN_LIMIT


def _merge_fields(lot: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    merged = dict(lot)
    for key, value in (fields or {}).items():
        if key == "parts":
            continue
        if not _filled(value):
            continue
        merged[key] = value
    return merged


def _blank_lot(fields: dict[str, Any]) -> dict[str, Any]:
    lot = {"freight": False}
    return _merge_fields(lot, fields)


def apply_verdict(lots: list[dict[str, Any]], payload: Any) -> list[dict[str, Any]]:
    """keep оставляет строку. fix меняет только присланные поля. Пустой ответ модели черновик не стирает."""
    base = [dict(lot) for lot in lots]
    if not isinstance(payload, dict):
        return [lot for lot in base if not lot.get("freight")]
    rows = payload.get("lots")
    if not isinstance(rows, list) or not rows:
        return [lot for lot in base if not lot.get("freight")]
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "keep").lower()
        if action == "add":
            fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
            out.append(_blank_lot(fields))
            continue
        index = row.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(base):
            continue
        used.add(index)
        lot = base[index]
        if action == "drop":
            continue
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        if action == "fix":
            out.append(_merge_fields(lot, fields))
        elif action == "split":
            parts = fields.get("parts") if isinstance(fields.get("parts"), list) else []
            if not parts:
                out.append(lot)
            else:
                for part in parts:
                    out.append(_merge_fields(lot, part if isinstance(part, dict) else {}))
        else:
            out.append(lot)
    for index, lot in enumerate(base):
        if index not in used and not lot.get("freight"):
            out.append(lot)
    return [lot for lot in out if not lot.get("freight")]


def goods_lots(draft: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(lot) for lot in (draft.get("lots") or []) if not lot.get("freight")]


def load_catalogs(references: list[tuple[Path, str]]) -> CatalogIndex:
    merged = CatalogIndex()
    for path, _display in references:
        if path.suffix.lower() not in _EXCEL:
            continue
        try:
            index = CatalogIndex.from_excel(path)
        except Exception:
            continue
        for key, row in index._by_article.items():
            if key not in merged._by_article:
                merged._by_article[key] = row
    return merged


def fill_from_catalog(lots: list[dict[str, Any]], catalog: CatalogIndex) -> None:
    """Пустые код и описание дописываются по точному артикулу. Уже заполненное число не затирается."""
    if catalog.size == 0:
        return
    for lot in lots:
        key = normalize_article(lot.get("vendor") or lot.get("model"))
        hit = catalog.lookup(key)
        if not hit:
            continue
        if not _filled(lot.get("hs")) and _filled(hit.get("tnved_code")):
            lot["hs"] = str(hit["tnved_code"])
        if not _filled(lot.get("description")):
            parts = [hit.get("description_en"), hit.get("description_ru")]
            text = " / ".join(str(part).strip() for part in parts if _filled(part))
            if text:
                lot["description"] = text


def _one_producer(lots: list[dict[str, Any]]) -> str:
    found: list[str] = []
    for lot in lots:
        value = str(lot.get("producer") or "").strip()
        if value and value not in found:
            found.append(value)
    return " / ".join(found)


def header_from_draft(draft: dict[str, Any], lots: list[dict[str, Any]]) -> dict[str, str]:
    def text(key: str) -> str:
        value = draft.get(key)
        return "" if value is None else str(value).strip()

    return {
        "seller": text("seller"),
        "buyer": text("buyer"),
        "seller_address": text("seller_address"),
        "buyer_address": text("buyer_address"),
        "contract_no": text("contract"),
        "contract_date": text("contract_date"),
        "invoice_no": text("invoice_no"),
        "invoice_date": text("invoice_date"),
        "delivery_terms": text("delivery"),
        "manufacturer": _one_producer(lots),
    }


def overlay_model_header(header: dict[str, str], payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return header
    model_header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    out = dict(header)
    mapping = {
        "seller": "seller",
        "buyer": "buyer",
        "contract": "contract_no",
        "invoice_no": "invoice_no",
    }
    for source, target in mapping.items():
        value = model_header.get(source)
        if _filled(value):
            out[target] = str(value).strip()
    return out


def _split_description(text: str) -> tuple[str, str, str]:
    raw = (text or "").strip()
    if " / " in raw:
        left, right = raw.split(" / ", 1)
        return left.strip(), right.strip(), raw
    if re.search(r"[А-Яа-яЁё]", raw):
        return "", raw, raw
    return raw, "", raw


def _is_meters(unit: str) -> bool:
    low = (unit or "").lower()
    if re.search(r"m2|м2|кв", low):
        return False
    return bool(re.search(r"метр|meter|metre|\bm\b", low))


def lots_to_rows(lots: list[dict[str, Any]], flags: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lot in lots:
        if lot.get("freight"):
            continue
        article = str(lot.get("vendor") or lot.get("model") or "").strip() or "-"
        desc_en, desc_ru, desc = _split_description(str(lot.get("description") or ""))
        pieces = lot.get("pieces")
        unit = str(lot.get("unit") or "").strip()
        commercial: dict[str, Any] = {}
        packing: dict[str, Any] = {}
        if _filled(pieces):
            commercial["qty"] = pieces
            if _is_meters(unit):
                packing["meters"] = pieces
        if _filled(unit):
            commercial["unit"] = unit
        if _filled(lot.get("price")):
            commercial["price"] = lot.get("price")
        if _filled(lot.get("amount")):
            commercial["amount"] = lot.get("amount")
        if _filled(lot.get("packages")):
            packing["rolls"] = lot.get("packages")
        for source, target in (
            ("width", "width"),
            ("area", "area"),
            ("net", "net_weight"),
            ("gross", "gross_weight"),
            ("volume", "volume"),
        ):
            if _filled(lot.get(source)):
                packing[target] = lot.get(source)
        customs: dict[str, Any] = {}
        hs = _plain_code(lot.get("hs"))
        hs_alt = _plain_code(lot.get("hs_alt"))
        if _filled(hs):
            customs["hs_code"] = hs
        if _filled(hs_alt):
            customs["tnved_code"] = hs_alt
        elif _filled(hs):
            customs["tnved_code"] = hs
        if desc:
            customs["description"] = desc
        if desc_en:
            customs["description_en"] = desc_en
        if desc_ru:
            customs["description_ru"] = desc_ru
        if _filled(lot.get("producer")):
            customs["manufacturer"] = lot.get("producer")
        if _filled(lot.get("origin")):
            customs["country"] = lot.get("origin")
        errors = []
        if lot.get("packages_conflict"):
            errors.append(_flag("rolls", "Места в документах разошлись, одно число не выбрано."))
        if lot.get("unit_conflict"):
            errors.append(_flag("qty", "Подпись единицы разошлась, количество не пересчитано."))
        rows.append(
            {
                "article": article,
                "model": str(lot.get("model") or article),
                "normalized_article": normalize_article(article),
                "commercial_data": commercial,
                "packing_data": packing,
                "customs_data": customs,
                "source_traces": {"sources": ["prepare"]},
                "validation_errors": errors,
            }
        )
    return rows


def _plain_code(value: Any) -> str:
    """Код в таблице без точек. 54.07.73.00.90.11 и 540773009011 — одно и то же написание."""
    text = " ".join(str(value or "").split())
    if text.count(".") < 2:
        return text
    digits = re.sub(r"\D", "", text)
    if 6 <= len(digits) <= 13 and re.fullmatch(r"[\d.\s]+", text):
        return digits
    return text


def _flag(field_name: str, message: str) -> dict[str, Any]:
    return {
        "field_name": field_name,
        "error_type": "MISMATCH",
        "severity": "YELLOW",
        "details": {"reason": message},
        "message": message,
    }


def review_files(documents: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    table = []
    for row in rows:
        commercial = row.get("commercial_data") or {}
        packing = row.get("packing_data") or {}
        customs = row.get("customs_data") or {}
        table.append(
            {
                "article": row.get("article"),
                "rolls": packing.get("rolls"),
                "qty": commercial.get("qty"),
                "meters": packing.get("meters"),
                "price": commercial.get("price"),
                "amount": commercial.get("amount"),
                "net_weight": packing.get("net_weight"),
                "gross_weight": packing.get("gross_weight"),
                "hs_code": customs.get("hs_code"),
                "description": customs.get("description"),
            }
        )
    pdfs = []
    excel = []
    for doc in documents:
        name = str(doc.get("name") or "")
        entry = {"filename": name, "table": table, "pages": [], "sheets": [], "meaning": doc.get("role") or ""}
        if name.lower().endswith(".pdf"):
            entry["kind"] = "pdf"
            pdfs.append(entry)
        else:
            entry["kind"] = "excel"
            excel.append(entry)
    return {"excel": excel, "pdfs": pdfs}
