"""Orchestrator: uploaded files -> canonical items (+ profile, header, warnings).

This is the single entry point the API calls. It:
  - reads every file safely (Excel via reader, PDF via the text layer),
  - turns each sheet into canonical rows and separates the reference catalog,
  - merges everything into one per-article table,
  - detects the output profile and the shipment letterhead,
  - reports friendly Russian per-file statuses instead of raw errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.parsing.header_extract import extract_header_fields, extract_header_from_letterheads, is_catalog_filename, merge_header_fields
from app.parsing.normalize import normalize_text
from app.transform.errors import FileReadError, humanize_for_file
from app.transform.extract import ExtractedSheet, extract_sheet, mapping_fits_sheet
from app.transform.merge import CanonicalItem, match_key, merge_documents
from app.transform.pdf import read_pdf
from app.transform.reader import EXCEL_SUFFIXES, read_workbook

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class FileOutcome:
    filename: str
    status: str          # ok | review | skipped
    message: str | None
    role_summary: str = ""


@dataclass
class TransformResult:
    items: list[CanonicalItem] = field(default_factory=list)
    profile: str = "beijing"
    header: dict[str, Any] = field(default_factory=dict)
    files: list[FileOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scanned_pdfs: list[str] = field(default_factory=list)
    # legacy-shaped per-file view for the model prompt + operator "what parser saw" panel
    sources: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# header / letterhead extraction
# --------------------------------------------------------------------------- #

# label -> (canonical header key, regex that matches the label at the start of a cell)
_HEADER_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("invoice_no", re.compile(r"^\s*(?:inv\.?\s*no\.?|invoice\s*no\.?|инв(?:ойс)?\s*(?:номер|№)|инв\s*номер)\s*[:.№]?\s*(.*)$", re.IGNORECASE)),
    ("invoice_date", re.compile(r"^\s*(?:invoice\s*date|дата\s*инв(?:ойса)?|дата)\s*[:.]?\s*(.*)$", re.IGNORECASE)),
    ("invoice_date", re.compile(r"^\s*date\s*:?\s*$", re.IGNORECASE)),
    ("contract_no", re.compile(r"^\s*contract\s*(?:no\.?|№)?\s*[:.№]?\s*(.*)$", re.IGNORECASE)),
    ("contract_no", re.compile(r"^\s*контракт\s*№?\s*[:.]?\s*(.*)$", re.IGNORECASE)),
    ("container", re.compile(r"^\s*(?:container\s*(?:no\.?)?|контейнер)\s*[:.]?\s*(.*)$", re.IGNORECASE)),
)

_STOP_VALUE = re.compile(
    r"^(buyer|seller|contract|invoice|date|container|продавец|покупатель|контракт|дата|"
    r"наименование|артикул|единица|ед\.?\s*измер|производитель)\b",
    re.IGNORECASE,
)

# Dates embedded in free-form letterhead titles (not only "Invoice date:" cells).
# Examples: "(for the shipment of 29-06-2026)", "dated 30.06.2026", "от 18.07.2026",
# "Invoice Date Jul.18,2026", "Shipment date: 2026/06/29".
_DATE_TOKEN = re.compile(
    r"("
    r"\d{1,2}[-./\s]\d{1,2}[-./\s]\d{2,4}"
    r"|"
    r"\d{4}[-./]\d{1,2}[-./]\d{1,2}"
    r"|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{1,2},?\s*\d{4}"
    r"|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{4}"
    r")",
    re.IGNORECASE,
)
_DATE_CONTEXT = re.compile(
    r"(?:shipment|shipped|отгруз|invoice\s*date|дата\s*инв|dated?|дата\b)",
    re.IGNORECASE,
)
# B/L / transport dates must not steal invoice_date when both appear in the letterhead.
_DATE_EXCLUDE = re.compile(r"\b(?:b/?l|bill\s*of\s*lading|etd|eta|sailing)\b", re.IGNORECASE)


def _clean_header_value(text: str) -> str:
    value = normalize_text(text).strip(" /.:-№")
    # cut a trailing second label glued into the same cell
    value = re.split(r"\s{2,}", value)[0]
    return value.strip(" .:-")


def _looks_like_date(value: str) -> bool:
    text = normalize_text(value or "")
    if not text or len(text) > 40:
        return False
    return bool(_DATE_TOKEN.fullmatch(text) or _DATE_TOKEN.search(text))


def _date_from_letterhead_cell(cell: str) -> str | None:
    """Pull a shipment/invoice date out of a free-form title cell."""
    text = normalize_text(cell or "")
    if not text or _DATE_EXCLUDE.search(text):
        return None
    if not _DATE_CONTEXT.search(text) and not (
        re.search(r"\binvoice\b|\binv\b|\binвойс\b|\bpacking\b", text, re.I)
        and _DATE_TOKEN.search(text)
    ):
        return None
    m = _DATE_TOKEN.search(text)
    if not m:
        return None
    return m.group(1).strip(" .")


def _extract_header(sheets: list[ExtractedSheet]) -> dict[str, Any]:
    ordered = sorted(sheets, key=lambda s: 0 if s.role in {"invoice", "mixed"} else 1)
    header = extract_header_from_letterheads([(s.source, s.letterhead) for s in ordered])
    # invoice-like sheets first so their letterhead wins
    for sheet in ordered:
        for row in sheet.letterhead:
            cells = [c for c in row]
            for i, cell in enumerate(cells):
                if not cell:
                    continue
                for key, pattern in _HEADER_LABELS:
                    if key in header:
                        continue
                    m = pattern.match(cell)
                    if not m:
                        continue
                    value = _clean_header_value(m.group(1) if m.lastindex else "")
                    if not value:
                        # value is in the next non-empty cell on the same row
                        for nxt in cells[i + 1:]:
                            if normalize_text(nxt):
                                value = _clean_header_value(nxt)
                                break
                    if key == "invoice_date" and value and not _looks_like_date(value):
                        # label matched but residual text is not a date (e.g. long title)
                        embedded = _date_from_letterhead_cell(cell) or _date_from_letterhead_cell(value)
                        value = embedded or ""
                    if value and not _STOP_VALUE.match(value) and len(value) <= 220:
                        header[key] = value
                # second pass: dates living inside titles without a classic "Invoice date:" label
                if "invoice_date" not in header:
                    embedded = _date_from_letterhead_cell(cell)
                    if embedded:
                        header["invoice_date"] = embedded
    if header.get("container") and not header.get("container_no"):
        header["container_no"] = header["container"]
    elif header.get("container_no") and not header.get("container"):
        header["container"] = header["container_no"]
    return header


# --------------------------------------------------------------------------- #
# profile detection
# --------------------------------------------------------------------------- #

_FABRIC_HS = ("5407", "5512", "5801", "5903", "6001", "4107", "5408")
_HARDWARE_HS = ("7318", "8302", "9401", "8412", "3926", "3921")


def detect_profile(items: list[CanonicalItem]) -> str:
    fabric = 0
    hardware = 0
    for it in items:
        f = it.fields
        code = str(f.get("hs_code") or f.get("customs_code") or "")
        if f.get("meters") or f.get("width") or code.startswith(_FABRIC_HS):
            fabric += 1
        unit = str(f.get("unit") or "").lower()
        if f.get("measurement") or f.get("boxes") or unit in {"pcs", "sets", "pair", "set"} or code.startswith(_HARDWARE_HS):
            hardware += 1
    return "18233" if fabric >= hardware and fabric else "beijing"


# --------------------------------------------------------------------------- #
# reading one file
# --------------------------------------------------------------------------- #

def _read_sheets(path: str, display: str) -> tuple[list[ExtractedSheet], list[str], bool]:
    suffix = Path(path).suffix.lower()
    sheets: list[ExtractedSheet] = []
    texts: list[str] = []
    scanned = False
    if suffix in EXCEL_SUFFIXES:
        for sheet in read_workbook(path):
            ex = extract_sheet(sheet)
            if ex:
                sheets.append(ex)
    elif suffix == ".pdf":
        result = read_pdf(path)
        if result.text:
            texts.append(result.text)
        inherited_mapping: dict[int, str] | None = None
        inherited_role: str | None = None
        stop_inherit = False
        for sheet in result.sheets:
            ex = extract_sheet(sheet)
            if ex is None and inherited_mapping and not stop_inherit and mapping_fits_sheet(sheet, inherited_mapping):
                ex = extract_sheet(
                    sheet,
                    inherited_mapping=inherited_mapping,
                    inherited_role=inherited_role,
                )
            if ex:
                sheets.append(ex)
                if ex.stopped_at_total:
                    stop_inherit = True
                    inherited_mapping = None
                    inherited_role = None
                else:
                    inherited_mapping = ex.mapping
                    inherited_role = ex.role
                    stop_inherit = False
        scanned = result.scanned
    elif suffix in IMAGE_SUFFIXES:
        scanned = True  # images are handled by the vision model, not here
    else:
        raise FileReadError("Формат файла не поддерживается. Загрузи Excel, PDF или изображение.")
    return sheets, texts, scanned


def _source_doc(
    path: str,
    display: str,
    sheets: list[ExtractedSheet],
    texts: list[str],
) -> dict[str, Any]:
    """Legacy-shaped doc so the model prompt + operator panel keep working."""
    suffix = Path(path).suffix.lower()
    kind = "pdf" if suffix == ".pdf" else "excel" if suffix in EXCEL_SUFFIXES else "image"
    lines: list[dict[str, Any]] = []
    sheet_names: list[str] = []
    roles: list[str] = []
    for ex in sheets:
        if ex.name and ex.name not in sheet_names:
            sheet_names.append(ex.name)
        roles.append(ex.role)
        for i, row in enumerate(ex.rows):
            raw = {k: v for k, v in row.fields.items() if v not in (None, "")}
            if row.article and "article" not in raw:
                raw = {"article": row.article, **raw}
            lines.append({"raw": raw, "article": row.article, "row_index": row.item_no or i})
    return {
        "filename": display,
        "file_path": path,
        "doc_type": roles[0] if roles else None,
        "mime_hint": kind,
        "sheets": sheet_names,
        "text_preview": "\n\n".join(t for t in ([_letterhead_preview(sheets)] + texts) if t)[:20000],
        "lines": lines,
    }


def _letterhead_preview(sheets: list[ExtractedSheet]) -> str:
    chunks: list[str] = []
    for ex in sheets:
        for row in ex.letterhead:
            parts = [c for c in row if c]
            if parts:
                chunks.append(" | ".join(parts))
    return "\n".join(chunks)


def transform_paths(
    paths: list[tuple[str, str]],
    catalog_names: set[str] | None = None,
) -> TransformResult:
    """paths = list of (absolute_path, display_name)."""
    result = TransformResult()
    catalog: dict[str, dict[str, Any]] = {}
    input_sheets: list[ExtractedSheet] = []
    scan_sheets: list[ExtractedSheet] = []
    all_texts: list[str] = []
    forced_catalog = {n.lower() for n in (catalog_names or set())}

    for path, display in paths:
        try:
            sheets, texts, scanned = _read_sheets(path, display)
        except FileReadError as exc:
            result.files.append(FileOutcome(filename=display, status="skipped", message=exc.ru))
            result.warnings.append(humanize_for_file(display, exc))
            continue
        except Exception as exc:  # noqa: BLE001
            msg = humanize_for_file(display, exc)
            result.files.append(FileOutcome(filename=display, status="skipped", message=msg))
            result.warnings.append(msg)
            continue

        all_texts.extend(texts)
        result.sources.append(_source_doc(path, display, sheets, texts))
        if scanned and not sheets:
            result.scanned_pdfs.append(display)
            result.files.append(FileOutcome(
                filename=display,
                status="review",
                message="Похоже на скан без текстового слоя - данные попробует прочитать модель по изображению.",
            ))
            continue
        if not sheets:
            result.files.append(FileOutcome(
                filename=display,
                status="review",
                message="Не нашёл таблицу с позициями. Проверь, что в файле есть таблица товаров.",
            ))
            continue

        force_catalog = is_catalog_filename(display) or display.lower() in forced_catalog
        is_scan_file = Path(path).suffix.lower() in {".pdf"} | IMAGE_SUFFIXES
        roles = []
        for ex in sheets:
            if force_catalog:
                ex.role = "catalog"
            if ex.role == "catalog":
                _index_catalog(catalog, ex)
            elif is_scan_file:
                scan_sheets.append(ex)
            else:
                input_sheets.append(ex)
            roles.append(ex.role)
        n = sum(len(ex.rows) for ex in sheets)
        result.files.append(FileOutcome(
            filename=display,
            status="ok" if n else "review",
            message=(
                f"файл {display} обработан кодом, нашлось {n} позиций"
                if n
                else "Таблица не собралась."
            ),
            role_summary=", ".join(sorted(set(roles))),
        ))

    if not input_sheets and scan_sheets:
        input_sheets = scan_sheets

    result.items = merge_documents(input_sheets, catalog=catalog)
    result.profile = detect_profile(result.items)
    result.header = _extract_header(input_sheets)
    result.header = merge_header_fields(result.header, extract_header_fields(result.sources))

    # Beijing-style goods get bilingual names from the reference catalog (ТЗ §5.2).
    # Without it the commercial numbers are fine, but Description stays empty - tell the user.
    has_catalog = bool(catalog) or any("catalog" in (f.role_summary or "") for f in result.files)
    if result.items and not has_catalog:
        missing_desc = sum(1 for it in result.items if not it.fields.get("description"))
        if missing_desc >= max(1, len(result.items) // 2):
            result.warnings.append(
                "Наименования и коды ТН ВЭД не подставлены: в загрузке нет справочника "
                "(файл вроде «сводная» / «описание»). Добавь его к комплекту или заполни описание вручную."
            )
    return result


def _index_catalog(catalog: dict[str, dict[str, Any]], ex: ExtractedSheet) -> None:
    for row in ex.rows:
        key = match_key(row.article)
        if not key or key in catalog:
            continue
        code = row.fields.get("customs_code") or row.fields.get("hs_code")
        catalog[key] = {
            "customs_code": code,
            "hs_code": row.fields.get("hs_code"),
            "description": row.fields.get("description"),
            "manufacturer": row.fields.get("manufacturer"),
            "country": row.fields.get("country"),
            "model": row.fields.get("model") or row.article,
        }
        model_key = match_key(str(row.fields.get("model") or ""))
        if model_key and model_key not in catalog:
            catalog[model_key] = catalog[key]


# --------------------------------------------------------------------------- #
# canonical item -> legacy row dict (kept API/exporter contract stable)
# --------------------------------------------------------------------------- #

def _split_description(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    raw = str(text).strip()
    left = right = None
    for sep in ("//", " / ", "/"):
        if sep not in raw:
            continue
        a, b = raw.split(sep, 1)
        a, b = a.strip(" /"), b.strip(" /")
        if a and b:
            left, right = a, b
            break
    if left is None:
        if re.search(r"[А-Яа-яЁё]", raw):
            return None, raw
        return raw, None
    left_cyr = bool(re.search(r"[А-Яа-яЁё]", left))
    right_cyr = bool(re.search(r"[А-Яа-яЁё]", right))
    if right_cyr and not left_cyr:
        return left or None, right or None
    if left_cyr and not right_cyr:
        return right or None, left or None
    return left or None, right or None


def canonical_to_rows(items: list[CanonicalItem]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for it in items:
        f = it.fields
        desc_en, desc_ru = _split_description(f.get("description"))
        commercial = {k: f[k] for k in ("qty", "unit", "price", "amount", "currency", "color") if f.get(k) not in (None, "")}
        packing = {k: f[k] for k in ("rolls", "boxes", "meters", "area", "width", "net_weight", "gross_weight", "volume", "measurement", "pcs_per_carton", "gm") if f.get(k) not in (None, "")}
        if f.get("_pack_group"):
            packing["pack_group"] = f["_pack_group"]
        if it.lines and len(it.lines) > 1:
            lots = []
            packing_lines = []
            for line in it.lines:
                lot = {k: line[k] for k in ("qty", "unit", "price", "amount", "color") if line.get(k) not in (None, "")}
                pline = {
                    k: line[k]
                    for k in (
                        "qty",
                        "net_weight",
                        "gross_weight",
                        "volume",
                        "boxes",
                        "rolls",
                        "measurement",
                        "pcs_per_carton",
                        "color",
                    )
                    if line.get(k) not in (None, "")
                }
                if lot.get("qty") not in (None, "") or lot.get("amount") not in (None, ""):
                    lots.append(lot)
                if pline:
                    packing_lines.append(pline)
            if lots:
                commercial["lots"] = lots
            if packing_lines:
                packing["lines"] = packing_lines
        customs: dict[str, Any] = {}
        if f.get("hs_code"):
            customs["hs_code"] = f["hs_code"]
        if f.get("customs_code"):
            customs["tnved_code"] = f["customs_code"]
        if customs.get("tnved_code") and not customs.get("hs_code"):
            customs["hs_code"] = customs["tnved_code"]
        if customs.get("hs_code") and not customs.get("tnved_code"):
            customs["tnved_code"] = customs["hs_code"]
        if desc_en:
            customs["description_en"] = desc_en
        if desc_ru:
            customs["description_ru"] = desc_ru
        if f.get("description") and not desc_en and not desc_ru:
            customs["description"] = f["description"]
        for k in ("manufacturer", "country"):
            if f.get(k):
                customs[k] = f[k]
        errors = []
        for fl in it.flags:
            details = {
                "sources": list(it.sources),
                "reason": fl.get("message", ""),
            }
            for key in ("was", "excel", "scan", "catalog"):
                if fl.get(key) not in (None, ""):
                    details[key] = fl[key]
            errors.append({
                "field_name": fl.get("field_name", "article"),
                "error_type": fl.get("error_type", "mismatch"),
                "severity": fl.get("severity", "yellow"),
                "details": details,
                "message": fl.get("message", ""),
            })
        rows.append({
            "article": it.article,
            "model": it.article,
            "normalized_article": it.key,
            "commercial_data": commercial,
            "packing_data": packing,
            "customs_data": customs,
            "source_traces": {"sources": it.sources},
            "invoice_subkit": None,
            "validation_errors": errors,
        })
    return rows
