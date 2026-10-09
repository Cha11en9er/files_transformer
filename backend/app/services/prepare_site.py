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
import unicodedata
from pathlib import Path
from typing import Any

from app.parsing.header_extract import is_catalog_filename
from app.parsing.normalize import normalize_article
from app.services.catalog import CatalogIndex

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from prepare_transform_code.numbers import parse_number
from prepare_transform_code.rolls import fold_rolls  # noqa: F401  (маршрут берёт её отсюда)
from prepare_transform_code.shipment import analyze
from prepare_transform_code.verdict import build_prompt

# Поля, которые модель часто присылает строкой с запятой («6,17»). Без разбора float() падает на сборке.
_NUMERIC_LOT = {
    "pieces",
    "packages",
    "price",
    "amount",
    "net",
    "net_primary",
    "gross",
    "gross_with_pallet",
    "unit_net",
    "volume",
    "area",
    "width",
    "gsm",
    "pallet_count",
    "pallet_weight",
}

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
        draft = analyze(dest)
        _add_ocr_hints(dest, draft)
        return draft


def _add_ocr_hints(folder: Path, draft: dict[str, Any]) -> None:
    """OCR для сканов как подсказка рядом с фото. Включается OCR_HINTS=1: на CPU он медленный и модель читает фото сама."""
    if os.getenv("OCR_HINTS", "").strip() not in {"1", "true", "yes"}:
        return
    for doc in draft.get("documents") or []:
        if doc.get("line_count") or doc.get("role") == "duplicate":
            continue
        path = folder / str(doc.get("name") or "")
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png"}:
            continue
        try:
            from app.parsing.ocr import ocr_image, ocr_pdf_pages

            text = ocr_pdf_pages(str(path))[0] if path.suffix.lower() == ".pdf" else ocr_image(str(path))[0]
        except Exception:
            continue
        text = " ".join(str(text or "").split())
        if text:
            doc["ocr_text"] = text[:6000]


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


DOUBT_SHARE = 0.3


def doubt_share(lots: list[dict[str, Any]]) -> float:
    """Доля строк, в которых модель не уверена или документы называют разные числа."""
    goods = [lot for lot in lots if not lot.get("freight")]
    if not goods:
        return 0.0
    doubtful = sum(
        1
        for lot in goods
        if lot.get("_confidence") == "low" or lot.get("conflicts") or lot.get("packages_conflict") or lot.get("unit_conflict")
    )
    return doubtful / len(goods)


def needs_second_model(lots: list[dict[str, Any]], flags: list[str] | None = None) -> bool:
    """Вторая модель нужна, когда таблица почти пустая, много сомнений или код сам отметил дыру мест.

    Пустые клетки мест у строк внутри слитого блока — норма (число один раз на блок).
    Смотрим packages_gap / weight_conflict, а не долю пустых packages.
    """
    if filled_base_columns(lots) <= THIN_COLUMN_LIMIT:
        return True
    if doubt_share(lots) >= DOUBT_SHARE:
        return True
    marks = set(flags or [])
    if marks & {"packages_gap", "weight_conflict"}:
        return True
    return False


def _as_number(value: Any) -> Any:
    """Число из ответа модели. «6,17» и «1.234,56» становятся float, неломаный текст остаётся текстом."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return value
    parsed = parse_number(text)
    return parsed if parsed is not None else value


def _merge_fields(lot: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    merged = dict(lot)
    for key, value in (fields or {}).items():
        if key == "parts":
            continue
        if not _filled(value):
            continue
        if key in _NUMERIC_LOT:
            value = _as_number(value)
        merged[key] = value
    return merged


def _blank_lot(fields: dict[str, Any]) -> dict[str, Any]:
    lot = {"freight": False}
    return _merge_fields(lot, fields)


def _fixed(lot: dict[str, Any], fields: dict[str, Any], reason: str, kind: str) -> dict[str, Any]:
    """Строка после решения модели. Что именно модель заменила, остаётся в служебных полях для флагов."""
    merged = _merge_fields(lot, fields)
    changes: dict[str, tuple[Any, Any]] = {}
    for key in (fields or {}):
        if key == "parts" or not _filled(lot.get(key)):
            continue
        if _filled(merged.get(key)) and merged.get(key) != lot.get(key):
            changes[key] = (lot.get(key), merged.get(key))
    merged["_verdict"] = kind
    merged["_reason"] = reason
    merged["_changes"] = changes
    return merged


def verdict_notes(lots: list[dict[str, Any]], payload: Any) -> list[str]:
    """Строки черновика, которые модель выбросила. Молча они не пропадают."""
    notes: list[str] = []
    rows = payload.get("lots") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return notes
    for row in rows:
        if not isinstance(row, dict) or str(row.get("action") or "").lower() != "drop":
            continue
        index = row.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(lots):
            continue
        lot = lots[index]
        title = str(lot.get("vendor") or lot.get("model") or lot.get("description") or f"строка {index + 1}")
        title = " ".join(title.split())[:80]
        reason = str(row.get("reason") or "").strip()
        notes.append(f"Модель убрала из черновика: {title}" + (f". {reason}" if reason else ""))
    return notes


def _tag(lot: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Уверенность и источник, которые модель назвала для строки. Низкая уверенность видна флагом."""
    confidence = str(row.get("confidence") or "").strip().lower()
    if confidence in {"low", "низкая"}:
        lot["_confidence"] = "low"
    source = str(row.get("source") or "").strip()
    if source:
        lot["_source"] = source[:80]
    return lot


def _normalize_numbers(lot: dict[str, Any]) -> dict[str, Any]:
    """Числа в лоте после вердикта. Модель может прислать «6,17» строкой, float() на сборке ломался."""
    out = dict(lot)
    for key in _NUMERIC_LOT:
        if key in out and _filled(out[key]):
            out[key] = _as_number(out[key])
    return out


def apply_verdict(lots: list[dict[str, Any]], payload: Any) -> list[dict[str, Any]]:
    """keep оставляет строку. fix меняет только присланные поля. Пустой ответ модели черновик не стирает."""
    base = [dict(lot) for lot in lots]
    if not isinstance(payload, dict):
        return [_normalize_numbers(lot) for lot in base if not lot.get("freight")]
    rows = payload.get("lots")
    if not isinstance(rows, list) or not rows:
        return [_normalize_numbers(lot) for lot in base if not lot.get("freight")]
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "keep").lower()
        reason = str(row.get("reason") or "").strip()
        if action == "add":
            fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
            added = _blank_lot(fields)
            added["_verdict"] = "add"
            added["_reason"] = reason
            out.append(_normalize_numbers(_tag(added, row)))
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
            out.append(_normalize_numbers(_tag(_fixed(lot, fields, reason, "fix"), row)))
        elif action == "split":
            parts = fields.get("parts") if isinstance(fields.get("parts"), list) else []
            if not parts:
                out.append(_normalize_numbers(lot))
            else:
                for part in parts:
                    out.append(
                        _normalize_numbers(_tag(_fixed(lot, part if isinstance(part, dict) else {}, reason, "split"), row))
                    )
        else:
            out.append(_normalize_numbers(_tag(dict(lot), row)))
    for index, lot in enumerate(base):
        if index not in used and not lot.get("freight"):
            out.append(_normalize_numbers(lot))
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


def _shared_producer(lots: list[dict[str, Any]]) -> str:
    """Один завод на всю поставку можно в шапку. Разные заводы строк в одну строку не склеивать."""
    from prepare_transform_code.fields import is_factory_list

    found: list[str] = []
    for lot in lots:
        value = str(lot.get("producer") or "").strip()
        if value and value not in found and not is_factory_list(value):
            found.append(value)
    if len(found) == 1:
        return found[0]
    return ""


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
        "consignee": text("consignee"),
        "consignee_address": text("consignee_address"),
        "payment_terms": text("payment"),
        "bank": text("bank"),
        # Валюта черновика — та, что у колонки цены, в написании документа (RMB, а не CNY). Иначе экспорт ставит дефолт.
        "currency": text("currency_printed") or text("currency"),
        "manufacturer": text("manufacturer") or _shared_producer(lots),
    }


_MODEL_HEADER = (
    ("seller", "seller"),
    ("buyer", "buyer"),
    ("contract", "contract_no"),
    ("invoice_no", "invoice_no"),
    # Валюту модель видит по колонке цены на фото и может поправить черновик.
    ("currency", "currency"),
    # Модель читает фото и видит бланк целиком, код — только текстовый слой. Названное моделью значение
    # заменяет черновик. Что именно заменено, видно в header_diff.
    ("invoice_date", "invoice_date"),
    ("contract_date", "contract_date"),
    ("seller_address", "seller_address"),
    ("buyer_address", "buyer_address"),
    ("consignee", "consignee"),
    ("consignee_address", "consignee_address"),
    ("delivery", "delivery_terms"),
)
# Номер читается как напечатан. Латинская C вместо кириллической (и наоборот) номер не меняет.
_IDENTIFIERS = {"contract_no", "invoice_no"}
_DATES = {"invoice_date", "contract_date"}

_LOOK_ALIKE = str.maketrans("авекмнорстух", "abekmhopctyx")
_CURRENCY_ALIAS = {
    "rmb": "cny",
    "yuan": "cny",
    "cnh": "cny",
    "¥": "cny",
    "us$": "usd",
    "$": "usd",
    "€": "eur",
    "euro": "eur",
}
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _fold(value: Any) -> str:
    """Запись для сравнения: регистр, кавычки, пробелы, знаки и похожие буквы двух алфавитов не считаются отличием."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[\W_]+", "", text.casefold().translate(_LOOK_ALIKE))


def _date_key(value: Any) -> str | None:
    """Дата в одном виде ГГГГ-ММ-ДД, чтобы 13/01/2026, 13.01.2026 и 2026-01-13 не считались разными."""
    text = " ".join(str(value or "").split())
    numeric = re.search(r"(\d{1,4})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{2,4})", text)
    if numeric:
        a, b, c = (int(part) for part in numeric.groups())
        if len(numeric.group(1)) == 4:
            year, month, day = a, b, c
        else:
            day, month, year = a, b, c + (2000 if c < 100 else 0)
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    named = re.search(r"([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*,?\s*(\d{4})", text)
    if named and named.group(1)[:3].lower() in _MONTHS:
        return f"{int(named.group(3)):04d}-{_MONTHS[named.group(1)[:3].lower()]:02d}-{int(named.group(2)):02d}"
    day_first = re.search(r"(\d{1,2})\s*([A-Za-z]{3,9})\.?\s*,?\s*(\d{4})", text)
    if day_first and day_first.group(2)[:3].lower() in _MONTHS:
        return f"{int(day_first.group(3)):04d}-{_MONTHS[day_first.group(2)[:3].lower()]:02d}-{int(day_first.group(1)):02d}"
    return None


def _currency_key(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return _CURRENCY_ALIAS.get(text, text)


def _same_value(field: str, old: Any, new: Any) -> bool:
    if field == "currency":
        return _currency_key(old) == _currency_key(new)
    if field in _DATES:
        left, right = _date_key(old), _date_key(new)
        if left and right:
            return left == right
    return _fold(old) == _fold(new)


def overlay_model_header(header: dict[str, str], payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return header
    model_header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    out = dict(header)
    for source, target in _MODEL_HEADER:
        value = model_header.get(source)
        if not _filled(value):
            continue
        text = str(value).strip()
        if _filled(out.get(target)) and _same_value(target, out.get(target), text):
            # Та же запись другими знаками. Валюта берётся в написании модели с фото, остальное остаётся как прочитал код.
            if target == "currency":
                out[target] = text
            continue
        out[target] = text
    return out


_HEADER_LABEL = {
    "seller": "продавец",
    "buyer": "покупатель",
    "contract_no": "номер контракта",
    "contract_date": "дата контракта",
    "invoice_no": "номер инвойса",
    "invoice_date": "дата инвойса",
    "currency": "валюта",
    "delivery_terms": "условие поставки",
    "seller_address": "адрес продавца",
    "buyer_address": "адрес покупателя",
    "consignee": "получатель",
    "consignee_address": "адрес получателя",
}


def header_diff(before: dict[str, str], after: dict[str, str]) -> list[dict[str, str]]:
    """Поля шапки, где модель заменила черновик кода или заполнила пустое. Только настоящие отличия, не кавычки и не знаки."""
    changes: list[dict[str, str]] = []
    for key, label in _HEADER_LABEL.items():
        old, new = before.get(key), after.get(key)
        if not _filled(new):
            continue
        if _filled(old) and _same_value(key, old, new):
            continue
        changes.append(
            {
                "field": key,
                "label": label,
                "before": " ".join(str(old or "").split()),
                "after": " ".join(str(new).split()),
                "kind": "replaced" if _filled(old) else "filled",
            }
        )
    return changes


def header_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Те же отличия одной строкой. Для заполненного моделью пустого поля строки нет: оно не заменяет чужое значение."""
    return [
        f"Модель заменила в шапке {item['label']}: было «{item['before'][:90]}», стало «{item['after'][:90]}»"
        for item in header_diff(before, after)
        if item["kind"] == "replaced"
    ]


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
        if not _filled(pieces):
            # Товар продан на вес: цена за кг, сумма = цена × нетто. Количество тогда равно нетто.
            pieces = _weight_quantity(lot)
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
        if _filled(lot.get("finish")):
            commercial["color"] = lot.get("finish")
        if _filled(lot.get("size")):
            commercial["size"] = lot.get("size")
        if _filled(lot.get("model")):
            commercial["model"] = lot.get("model")
        if _filled(lot.get("packages")):
            packing["rolls"] = lot.get("packages")
        # Вид места и паллеты — отдельно от числа мест. Иначе «2150 коробок и 20 паллет» превращается в 2150 рулонов.
        if _filled(lot.get("package_type")):
            packing["package_type"] = lot.get("package_type")
        if _filled(lot.get("pallet_count")):
            packing["pallets"] = lot.get("pallet_count")
        if _filled(lot.get("gross_with_pallet")):
            packing["gross_weight_with_pallet"] = lot.get("gross_with_pallet")
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
        if lot.get("hs_alt_shipper") and _filled(hs) and _filled(hs_alt):
            # Таможенный код поставщика стоит в колонке HS, ТН ВЭД остаётся основным.
            customs["hs_code"] = hs_alt
            customs["tnved_code"] = hs
        else:
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
            from prepare_transform_code.fields import is_factory_list as _factory_list

            if not _factory_list(lot.get("producer")):
                customs["manufacturer"] = lot.get("producer")
        if _filled(lot.get("brand")):
            customs["brand"] = lot.get("brand")
        if _filled(lot.get("origin")):
            customs["country"] = lot.get("origin")
        errors = []
        if lot.get("packages_conflict"):
            errors.append(_flag("rolls", "Места в документах разошлись, одно число не выбрано."))
        if lot.get("unit_conflict"):
            errors.append(_flag("qty", "Подпись единицы разошлась, количество не пересчитано."))
        errors.extend(_lot_flags(lot))
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


def _weight_quantity(lot: dict[str, Any]) -> float | None:
    unit = str(lot.get("unit") or "").casefold()
    if not re.search(r"\b(?:kgs?|кг)\b", unit):
        return None
    price = _as_number(lot.get("price"))
    amount = _as_number(lot.get("amount"))
    net = _as_number(lot.get("net"))
    if not (
        isinstance(price, (int, float))
        and isinstance(amount, (int, float))
        and isinstance(net, (int, float))
        and price
    ):
        return None
    if abs(float(amount) / float(price) - float(net)) <= max(0.5, abs(float(net)) * 0.002):
        return float(net)
    return None


_ROW_FIELD = {
    "vendor": ("article", "артикул"),
    "model": ("article", "модель"),
    "description": ("description", "описание"),
    "pieces": ("qty", "количество"),
    "packages": ("rolls", "места"),
    "price": ("price", "цена"),
    "amount": ("amount", "сумма"),
    "net": ("net_weight", "нетто"),
    "gross": ("gross_weight", "брутто"),
    "volume": ("volume", "объём"),
    "area": ("area", "площадь"),
    "hs": ("hs_code", "код"),
    "hs_alt": ("tnved_code", "второй код"),
    "unit": ("qty", "единица"),
    "origin": ("country", "страна"),
    "producer": ("manufacturer", "производитель"),
}


def _show(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
        return text.replace(",", " ")
    return str(value)


def _lot_flags(lot: dict[str, Any]) -> list[dict[str, Any]]:
    """Что код и модель сочли спорным в этой строке. Флаг стоит у той клетки, к которой относится."""
    out: list[dict[str, Any]] = []
    for key, other in (lot.get("conflicts") or {}).items():
        field, label = _ROW_FIELD.get(key, ("article", key))
        out.append(
            _flag(
                field,
                f"Второй документ называет другое число ({label}: {_show(other)}, в строке {_show(lot.get(key))}). Одно число не выбрано.",
            )
        )
    if lot.get("weight_suspect"):
        out.append(_flag("net_weight", "Нетто выглядит неправдоподобно по весу на штуку. Проверь по документам."))
    gross, with_pallet = _as_number(lot.get("gross")), _as_number(lot.get("gross_with_pallet"))
    if (
        isinstance(gross, (int, float))
        and isinstance(with_pallet, (int, float))
        and abs(float(gross) - float(with_pallet)) > 0.05
    ):
        extra = ""
        if _filled(lot.get("pallet_count")) and _filled(lot.get("pallet_weight")):
            extra = f" ({_show(lot.get('pallet_count'))} палл. по {_show(lot.get('pallet_weight'))})"
        out.append(
            _flag(
                "gross_weight",
                f"Два брутто: без паллет {_show(gross)}, с паллетами {_show(with_pallet)}{extra}. Оба настоящие, одно не выбрано.",
            )
        )
    if lot.get("_confidence") == "low":
        where = f" Источник: {lot.get('_source')}." if lot.get("_source") else ""
        out.append(_flag("article", "Модель не уверена в этой строке, проверь по документу." + where))
    kind = lot.get("_verdict")
    reason = str(lot.get("_reason") or "").strip()
    suffix = f" {reason}" if reason else ""
    if kind == "add":
        out.append(_flag("article", "Строки не было в черновике кода, модель добавила её по фото." + suffix))
    elif kind == "split":
        out.append(_flag("article", "Модель разбила строку черновика на несколько по фото." + suffix))
    for key, (old, new) in (lot.get("_changes") or {}).items():
        field, label = _ROW_FIELD.get(key, ("article", key))
        out.append(_flag(field, f"Модель заменила {label}: было {_show(old)}, стало {_show(new)}." + suffix))
    return out


def fee_rows(freights: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Сборы (упаковка, консолидация, доставка) входят в деньги поставки и не входят в штуки."""
    rows: list[dict[str, Any]] = []
    for fee in freights or []:
        amount = fee.get("amount")
        if not _filled(amount):
            continue
        description = " ".join(str(fee.get("description") or "Сбор").split())
        desc_en, desc_ru, desc = _split_description(description)
        customs: dict[str, Any] = {"description": desc}
        if desc_en:
            customs["description_en"] = desc_en
        if desc_ru:
            customs["description_ru"] = desc_ru
        rows.append(
            {
                "article": "-",
                "model": "-",
                "normalized_article": "",
                "commercial_data": {"amount": amount},
                "packing_data": {},
                "customs_data": customs,
                "source_traces": {"sources": ["prepare"], "fee": True},
                "validation_errors": [
                    _flag("amount", "Это сбор, не товар: сумма входит в деньги поставки, в штуки не входит.")
                ],
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


def review_files(
    documents: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    tables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Вкладки файлов. Если код отдал строки каждого файла, вкладка показывает их, а не общую таблицу поставки."""
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
                "brand": customs.get("brand"),
                "manufacturer": customs.get("manufacturer"),
                "country": customs.get("country"),
                "color": commercial.get("color"),
                "size": commercial.get("size"),
            }
        )
    pdfs = []
    excel = []
    for doc in documents:
        name = str(doc.get("name") or "")
        own = (tables or {}).get(name)
        rows = own["rows"] if own is not None else table
        if not _review_has_rows(rows):
            continue
        entry = {
            "filename": name,
            "table": rows,
            "headers": (own or {}).get("headers") or [],
            "note": (own or {}).get("note") or "",
            "text": "",
            "total_rows": (own or {}).get("total_rows") or len(rows),
            "pages": [],
            "sheets": [],
            "meaning": _ROLE_TITLE.get(str(doc.get("role") or ""), doc.get("role") or ""),
        }
        base = name.split(" / ")[0].lower()
        if base.endswith(".pdf"):
            entry["kind"] = "pdf"
            pdfs.append(entry)
        elif base.endswith((".jpg", ".jpeg", ".png")):
            entry["kind"] = "image"
            pdfs.append(entry)
        else:
            entry["kind"] = "excel"
            excel.append(entry)
    return {"excel": excel, "pdfs": pdfs}


def _review_has_rows(rows: list[dict[str, Any]] | None) -> bool:
    for row in rows or []:
        if not row:
            continue
        if any(
            row.get(key) not in (None, "", [])
            for key in (
                "article",
                "description",
                "hs_code",
                "customs_code",
                "qty",
                "amount",
                "net_weight",
                "gross_weight",
                "price",
                "rolls",
                "meters",
            )
        ):
            return True
        if row.get("raw"):
            return True
    return False


_ROLE_TITLE = {
    "invoice": "инвойс",
    "packing": "пакинг",
    "specification": "спецификация",
    "proforma": "проформа",
    "duplicate": "дубль",
    "image": "картинка",
    "scan": "скан",
    "unknown": "роль не определена",
}
