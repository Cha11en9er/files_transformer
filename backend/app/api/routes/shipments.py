"""Shipment REST API — Word spec §7 operator workspace."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.models.enums import (
    DocType,
    PermitDatabaseSource,
    ProfileType,
    ShipmentStatus,
)
from app.parsing.header_extract import enrich_header_from_goods, export_header_fields
from app.parsing.pdf_extractor import sniff_kind
from app.parsing.pipeline import parse_upload
from app.parsing.schemas import ParsedDocument
from app.parsing.user_messages import humanize_exception, humanize_message
from app.schemas.api import (
    AttachColumnRequest,
    ExportRequest,
    FileOut,
    ItemOut,
    ModelReviewOut,
    PermitSearchOut,
    ShipmentCreateResponse,
    ValidationErrorOut,
)
from app.services.opencode_review import review_with_opencode
from app.services.pdf_pages import collect_vision_images
from app.services.prepare_site import (
    apply_verdict,
    fee_rows,
    fill_from_catalog,
    fold_rolls,
    goods_lots,
    header_diff,
    header_from_draft,
    load_catalogs,
    lots_to_rows,
    model_spec,
    needs_second_model,
    overlay_model_header,
    read_goods,
    review_files,
    split_uploads,
    verdict_notes,
    verdict_prompt,
)
from app.services.scan_reconcile import compute_excel_totals
from app.services.export import build_export_preview, export_18233, export_beijing
from app.services.export_18233_templates import export_18233_from_templates
from app.services.export_style import resolve_shipment_title, safe_export_stem
from app.services.materials_18233 import kit_sources, materials_available
from app.services.profile_18233 import parse_bundle, reconcile_18233


def _model_chip(spec: str) -> str:
    """На сайте без имён моделей: обычная или улучшенная."""
    low = (spec or "").lower()
    if "grok" in low:
        return "улучшенная модель"
    if "qwen" in low:
        return "обычная модель"
    return "обычная модель" if spec else "модель"
from app.services.reconcile import items_to_dicts, reconcile_documents
from app.transform.service import canonical_to_rows, transform_paths

SUPPORTED_UPLOAD_SUFFIXES = {
    ".xlsx",
    ".xls",
    ".xlsm",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}


_CRITICAL_PARSE_PREFIXES = (
    "parse_failed:",
    "pdf_read_failed:",
)


def is_critical_parse_warning(warning: str) -> bool:
    text = (warning or "").lower()
    return any(text.startswith(prefix) for prefix in _CRITICAL_PARSE_PREFIXES)


def classify_parse_result(warnings: list[str], *, had_exception: bool = False) -> tuple[str, str | None]:
    """ok | review | skipped. Skipped only for a broken file, not for a messy table."""
    if had_exception or any(is_critical_parse_warning(item) for item in warnings):
        message = next((item for item in warnings if is_critical_parse_warning(item)), None)
        return "skipped", humanize_message(message or "файл не удалось прочитать")
    soft = [item for item in warnings if item]
    if soft:
        return "review", "; ".join(humanize_message(item) for item in soft[:4])
    return "ok", None


SKIP_DIR_NAMES = {"ДС", "DS", "__MACOSX"}


def _is_supported_upload(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES


def _validate_upload_filename(filename: str) -> None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Формат «{suffix or 'без расширения'}» не поддерживается. "
                "Допустимы Excel (.xlsx, .xls), PDF и изображения (.png, .jpg)."
            ),
        )


def _allow_ocr_for_upload(path: Path) -> bool:
    return sniff_kind(str(path)) in {"pdf", "image"}


def _guess_doc_type(filename: str, parsed_type: DocType | None) -> DocType | None:
    name_l = filename.lower()
    if any(token in name_l for token in ("сводная", "svodn", "справочник", "catalog")):
        return DocType.CATALOG
    kind = _classify_18233_filename(filename)
    if kind == "packing" and parsed_type in {None, DocType.INVOICE}:
        return DocType.PACKING_LIST
    if parsed_type is not None:
        return parsed_type
    if kind == "invoice":
        return DocType.INVOICE
    if kind == "packing":
        return DocType.PACKING_LIST
    if kind == "specification":
        return DocType.SPECIFICATION
    if name_l.endswith(".pdf") or name_l.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        if any(token in name_l for token in ("описание", "description", "desc")):
            return DocType.SPECIFICATION
        if any(token in name_l for token in ("permit", "разреш", "рд", "rd")):
            return DocType.PERMIT
    return parsed_type


def _classify_18233_filename(filename: str) -> str | None:
    """Map upload name → invoice | packing | specification (EN + RU)."""
    name = filename.upper()
    stem = Path(filename).stem.upper()
    if (
        "INVOICE" in name
        or "ИНВОЙС" in name
        or stem.endswith("_CI")
        or stem.endswith("-CI")
        or stem.endswith(" CI")
        or " COMMERCIAL INVOICE" in f" {name}"
    ):
        return "invoice"
    if (
        "-PL" in name
        or "_PL." in name
        or stem.endswith("_PL")
        or stem.endswith("-PL")
        or "PACKING" in name
        or name.endswith("PL.XLSX")
        or name.endswith("PL.PDF")
        or "ПАКИНГ" in name
        or "УПАКОВ" in name
    ):
        return "packing"
    if "SPEC" in name or "СПЕЦИФ" in name:
        return "specification"
    return None


def _is_ready_etalon_filename(filename: str) -> bool:
    """Customer «02_Готовые» / our export — not source inputs from «01_Исходники»."""
    name = filename.upper()
    if "ЦВЕТАМ" in name:
        return True
    if "YS-RMB-EXW" in name or "SPECIFICATION.XLS" in name:
        return False
    # Russian ready templates look like «18233 ИНВОЙС 626-1.xlsx»
    if any(token in name for token in ("ИНВОЙС", "ПАКИНГ", "СПЕЦИФ")) and "18233" in name:
        return True
    return False


def _is_skippable_upload(filename: str) -> bool:
    raw = (filename or "").replace("\\", "/")
    path = Path(raw)
    if path.name.upper() in {"THUMBS.DB", ".DS_STORE"}:
        return True
    if any(part.upper() in SKIP_DIR_NAMES for part in Path(raw).parts):
        return True
    return not _is_supported_upload(path.name)


def _upload_fingerprint(upload: UploadFile) -> str:
    pos = upload.file.tell()
    data = upload.file.read()
    upload.file.seek(pos)
    return f"{len(data)}:{hashlib.sha256(data).hexdigest()}"


def _dedupe_uploads(files: list[UploadFile]) -> list[UploadFile]:
    seen: dict[str, str] = {}
    kept: list[UploadFile] = []
    for upload in files:
        try:
            mark = _upload_fingerprint(upload)
        except Exception:
            kept.append(upload)
            continue
        if mark in seen:
            continue
        seen[mark] = upload.filename or ""
        kept.append(upload)
    return kept


def _filter_upload_batch(files: list[UploadFile]) -> list[UploadFile]:
    kept = [upload for upload in files if not _is_skippable_upload(upload.filename or "")]
    sources = [upload for upload in kept if not _is_ready_etalon_filename(upload.filename or "")]
    if sources and len(sources) < len(kept):
        kept = sources
    return _dedupe_uploads(kept)


_JOB_RE = re.compile(r"\b18\d{3}\b")
_KIT_RE = re.compile(r"\b\d{2,4}-\d\b")


def mixed_shipment_error(filenames: list[str]) -> str | None:
    """Refuse a dump that mixes two Hangzhou kits or two broker job numbers.

    Job/kit tokens in *filenames* of one shipment (invoice 18049 + spec 18049)
    are the same job. Do not treat each filename as a separate folder.
    """
    names = [name.replace("\\", "/") for name in filenames if name]
    blob = " ".join(names)
    kits = {m.group(0).lower() for m in _KIT_RE.finditer(blob)}
    if len(kits) > 1:
        listed = ", ".join(sorted(kits))
        return (
            f"В загрузке одновременно указаны комплекты {listed}. "
            "Загрузите документы одного комплекта."
        )
    jobs = set(_JOB_RE.findall(blob))
    if len(jobs) > 1:
        listed = ", ".join(sorted(jobs))
        return (
            f"В загрузке несколько комплектов ({listed}). "
            "Загрузите документы одного комплекта."
        )
    return None


def _looks_like_18233(paths: list[Path]) -> bool:
    """Zhongfang-style Invoice + PL + Spec kits use the dedicated parser."""
    names = [path.name.upper() for path in paths]
    blob = " ".join(names)
    if any(token in blob for token in ("YS-RMB-EXW", "RMB-EXW", "626-1", "626-2", "ZHONGFANG", "MENGMA")):
        return True
    return _has_separate_18233_kit(paths)


def _has_separate_18233_kit(paths: list[Path]) -> bool:
    if len(paths) < 2:
        return False
    excel_roles = {
        kind
        for path in paths
        if path.suffix.lower() in {".xlsx", ".xls", ".xlsm"}
        and (kind := _classify_18233_filename(path.name))
    }
    return "invoice" in excel_roles and ("packing" in excel_roles or "specification" in excel_roles)


def _detect_kit_code(paths: list[Path]) -> str | None:
    blob = " ".join(p.name for p in paths).upper()
    if "626-2" in blob:
        return "626-2"
    if "626-1" in blob:
        return "626-1"
    return None


def _complete_18233_kit(saved_paths: list[Path]) -> tuple[list[Path], list[str]]:
    """If PL/Spec missing, pull them from local MVP materials for the same kit."""
    present = {k: p for p in saved_paths if (k := _classify_18233_filename(p.name))}
    missing = {"invoice", "packing", "specification"} - set(present)
    if not missing or not materials_available():
        return saved_paths, []
    kit = _detect_kit_code(saved_paths)
    if kit is None:
        return saved_paths, []
    try:
        sources = kit_sources(kit)
    except Exception:
        return saved_paths, []

    completed = list(saved_paths)
    notes: list[str] = []
    known = {p.resolve() for p in completed}
    for role in ("invoice", "packing", "specification"):
        if role in present:
            continue
        src = sources.get(role)
        if src is None or not src.exists():
            continue
        if src.resolve() in known:
            continue
        completed.append(src)
        known.add(src.resolve())
        notes.append(src.name)
    return completed, notes


_ROLE_RU = {
    "invoice": "инвойс",
    "packing": "пакинг",
    "specification": "спецификация",
    "proforma": "проформа",
}

_FLAG_RU = {
    "hs_conflict": "два кода рядом, оба оставлены",
    "manufacturer_conflict": "в документах разный производитель",
    "delivery_conflict": "два разных условия поставки",
    "weight_conflict": "два пакинга с разным весом",
    "packages_conflict": "места в документах разошлись",
    "unit_conflict": "подпись единицы разошлась",
    "foreign_document": "в комплекте чужой лист",
    "weight_suspect": "вес строки выглядит неправдоподобно, проверь по документам",
    "values_conflict": "документы называют разные числа для одной строки",
}


def _stated_for_review(stated: dict[str, Any]) -> dict[str, Any]:
    pieces = stated.get("pieces")
    return {
        "qty": pieces,
        "meters": pieces,
        "rolls": stated.get("packages"),
        "amount": stated.get("amount"),
        "net_weight": stated.get("net"),
        "gross_weight": stated.get("gross"),
        "area": stated.get("area"),
    }


def _totals_differ(excel, stated: dict[str, Any]) -> bool:
    pairs = (
        ("rolls", "rolls"),
        ("meters", "meters"),
        ("qty", "qty"),
        ("amount", "amount"),
        ("net_weight", "net_weight"),
        ("gross_weight", "gross_weight"),
        ("area", "area"),
    )
    for left, right in pairs:
        file_value = stated.get(right)
        table_value = getattr(excel, left, None)
        if file_value is None or table_value is None:
            continue
        if abs(float(file_value) - float(table_value)) > 0.05:
            return True
    return False


router = APIRouter(prefix="/api/v1/shipments", tags=["shipments"])


def _write_upload(tmp_dir: Path, upload: UploadFile) -> Path:
    raw = (upload.filename or "upload.bin").replace("\\", "/")
    safe_name = Path(raw).name or "upload.bin"
    dest = tmp_dir / safe_name
    if dest.exists():
        dest = tmp_dir / f"{dest.stem}_{uuid.uuid4().hex[:8]}{dest.suffix}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return dest


def _make_file_out(
    *,
    filename: str,
    doc_type: DocType | None,
    ocr_confidence: float | None,
    parse_status: str,
    parse_message: str | None,
) -> FileOut:
    return FileOut(
        id=uuid.uuid4(),
        filename=filename,
        doc_type=doc_type,
        ocr_confidence=ocr_confidence,
        parse_status=parse_status,
        parse_message=parse_message,
    )


def _items_from_rows(rows: list[dict[str, Any]]) -> list[ItemOut]:
    items: list[ItemOut] = []
    for row in rows:
        flags: list[ValidationErrorOut] = []
        for flag in row.get("validation_errors") or []:
            flags.append(
                ValidationErrorOut(
                    id=uuid.uuid4(),
                    field_name=flag["field_name"],
                    error_type=flag["error_type"],
                    severity=flag["severity"],
                    details=flag.get("details"),
                    message=flag.get("message"),
                    resolved=bool(flag.get("resolved") or False),
                )
            )
        items.append(
            ItemOut(
                id=uuid.uuid4(),
                article=row.get("article"),
                model=row.get("model"),
                normalized_article=row.get("normalized_article") or "",
                commercial_data=row.get("commercial_data") or {},
                packing_data=row.get("packing_data") or {},
                extra_columns=row.get("extra_columns") or {},
                customs_data=row.get("customs_data") or {},
                source_traces=row.get("source_traces") or {},
                validation_errors=flags,
            )
        )
    return items


def _iter_create_events(
    *,
    title: str,
    profile_type: ProfileType,
    incoming: list[UploadFile],
    catalog_names: set[str] | None = None,
) -> Iterator[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="parse_") as tmp:
        tmp_dir = Path(tmp)
        file_outs: list[FileOut] = []
        skipped_count = 0
        header_fields: dict[str, Any] = {}
        active_profile = profile_type
        total = len(incoming)
        yield {"event": "start", "total": total}

        saved: list[tuple[str, str]] = []
        for index, upload in enumerate(incoming, start=1):
            path = _write_upload(tmp_dir, upload)
            display_name = Path((upload.filename or path.name).replace("\\", "/")).name
            saved.append((str(path), display_name))
            yield {"event": "progress", "current": index, "total": total, "filename": display_name, "stage": "parse"}

        yield {"event": "progress", "current": total, "total": total, "filename": "сверка позиций", "stage": "reconcile"}

        def _while_busy(message: str, fn):
            """Шаг виден сразу. Пока функция работает, раз в 12 секунд уходит пульс, иначе прокси рвёт молчащее соединение."""
            started = time.monotonic()
            yield {
                "event": "progress",
                "current": total,
                "total": total,
                "filename": message,
                "stage": "reconcile",
                "message": message,
            }
            box: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

            def _call() -> None:
                try:
                    box.put(("ok", fn()))
                except Exception as exc:  # noqa: BLE001 — surface to stream consumer
                    box.put(("err", exc))

            worker = threading.Thread(target=_call, name="parse-busy", daemon=True)
            worker.start()
            waited = 0
            while worker.is_alive():
                worker.join(timeout=12.0)
                if worker.is_alive():
                    waited += 12
                    yield {
                        "event": "progress",
                        "current": total,
                        "total": total,
                        "filename": message,
                        "stage": "reconcile",
                        "message": f"{message} ({waited} с)",
                    }
            status, payload = box.get()
            if status == "err":
                raise payload
            elapsed = max(0, round(time.monotonic() - started))
            yield {
                "event": "progress",
                "current": total,
                "total": total,
                "filename": message,
                "stage": "reconcile",
                "message": f"{message} ({elapsed} с)",
            }
            return payload

        goods, references = split_uploads(saved, catalog_names)
        draft = yield from _while_busy("Чтение файлов кодом", lambda: read_goods(goods))
        catalog = load_catalogs(references)
        prompt = verdict_prompt(draft)
        lots = goods_lots(draft)
        vision_dir = tmp_dir / "vision"

        def _photos() -> list:
            try:
                return collect_vision_images([path for path, _name in goods], vision_dir)
            except Exception:
                return []

        vision_pages = yield from _while_busy("Фото страниц", _photos)
        yield {
            "event": "progress",
            "current": total,
            "total": total,
            "filename": "Фото страниц",
            "stage": "reconcile",
            "message": f"Фото страниц, {len(vision_pages)} кадров",
        }
        first_model = model_spec(1)
        second_model = model_spec(2)
        first_chip = _model_chip(first_model)
        second_chip = _model_chip(second_model) if second_model else ""
        yield {
            "event": "progress",
            "current": total,
            "total": total,
            "filename": first_model or "модель",
            "stage": "model",
            "message": f"Обычная модель: сверка черновика с фото",
        }

        def _ask(spec: str, stage: str = "model") -> dict[str, Any]:
            """Спросить модель. Ошибка модели не рвёт поток: отдаём status=error и оставляем черновик кода."""
            box: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
            label = "Улучшенная модель" if stage == "model2" else "Обычная модель"

            def _call() -> None:
                try:
                    box.put(
                        (
                            "ok",
                            review_with_opencode(
                                snapshot={"title": title or "shipment", "context": {"excel": [], "pdfs": []}},
                                pages=vision_pages,
                                user_prompt=prompt,
                                model_override=spec or None,
                            ),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — вернём в поток как status=error, без raise
                    box.put(("err", exc))

            worker = threading.Thread(target=_call, name="opencode-verdict", daemon=True)
            worker.start()
            waited = 0
            while worker.is_alive():
                worker.join(timeout=12.0)
                if worker.is_alive():
                    waited += 12
                    yield {
                        "event": "progress",
                        "current": total,
                        "total": total,
                        "filename": spec or "модель",
                        "stage": stage,
                        "message": f"{label} всё ещё отвечает ({waited} с)",
                    }
            status, payload = box.get()
            if status == "err":
                err_text = humanize_exception(payload)
                yield {
                    "event": "progress",
                    "current": total,
                    "total": total,
                    "filename": spec or "модель",
                    "stage": stage,
                    "message": f"{label}: не ответила, оставляем черновик кода",
                }
                return {
                    "status": "error",
                    "model": spec or "",
                    "model_label": _model_chip(spec),
                    "raw_text": "",
                    "payload": None,
                    "error": err_text,
                    "image_count": len(vision_pages),
                    "excel_attached": False,
                    "excel_files": [],
                    "meaning": f"{label} не ответила. В таблице черновик кода.",
                    "header": {},
                    "tables": [],
                    "totals": None,
                    "items": [],
                    "context": {"excel": [], "pdfs": []},
                }
            return payload

        review_dict: dict[str, Any] = {"status": "skipped", "model": first_model, "error": None}
        verdict_dropped: list[str] = []
        second_used = False
        def _apply_model(target_lots: list[dict[str, Any]], payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
            try:
                applied = apply_verdict(target_lots, payload)
                notes = verdict_notes(list(draft.get("lots") or []), payload)
                return applied, notes
            except Exception as exc:  # noqa: BLE001 — кривой вердикт не должен ронять поставку
                return target_lots, [f"Вердикт модели не применился ({humanize_exception(exc)}). Оставлен черновик кода."]

        if first_model:
            review_dict = yield from _ask(first_model, "model")
            if review_dict.get("status") == "ok":
                lots, verdict_dropped = _apply_model(goods_lots(draft), review_dict.get("payload"))
            elif review_dict.get("error"):
                verdict_dropped.append(f"Модель: {review_dict.get('error')}. Показан черновик кода.")
        if second_model and needs_second_model(lots, draft.get("flags")):
            second_used = True
            yield {
                "event": "progress",
                "current": total,
                "total": total,
                "filename": second_model,
                "stage": "model2",
                "message": "Улучшенная модель: мало столбцов, сомнения или дыра мест",
            }
            second = yield from _ask(second_model, "model2")
            if second.get("status") == "ok":
                lots, verdict_dropped = _apply_model(goods_lots(draft), second.get("payload"))
                review_dict = second
            elif second.get("error"):
                # Первая модель (или код) уже в lots — вторую ошибку только помечаем.
                if review_dict.get("status") == "ok":
                    verdict_dropped.append(
                        f"Улучшенная модель: {second.get('error')}. Оставляем вердикт обычной."
                    )
                else:
                    review_dict = second
                    verdict_dropped.append(
                        f"Улучшенная модель: {second.get('error')}. Показан черновик кода."
                    )
        shutil.rmtree(vision_dir, ignore_errors=True)

        def _assemble() -> tuple[list[dict[str, Any]], list[str], dict[str, Any], list[dict[str, str]], list[str], list[dict[str, Any]]]:
            filled = list(lots)
            fill_from_catalog(filled, catalog)
            # Рулоны одного дизайна идут одной строкой. Каждый рулон остаётся виден на вкладке файла.
            filled, fold_notes = fold_rolls(filled, draft.get("spec_rows"))
            flags_now = [
                flag
                for flag in (draft.get("flags") or [])
                if flag != "packages_conflict" or any(lot.get("packages_conflict") for lot in filled)
            ]
            header_fields = header_from_draft(draft, filled)
            header_changed: list[dict[str, str]] = []
            if review_dict.get("status") == "ok":
                draft_header = dict(header_fields)
                header_fields = overlay_model_header(header_fields, review_dict.get("payload"))
                header_changed = header_diff(draft_header, header_fields)
            header_notes = list(draft.get("notes") or []) + verdict_dropped + fold_notes
            reconciled = lots_to_rows(filled, flags_now)
            # Сборы идут отдельными строками без количества: деньги поставки их содержат, штуки нет.
            reconciled = reconciled + fee_rows(draft.get("freights"))
            return filled, flags_now, header_fields, header_changed, header_notes, reconciled

        def _assemble_code_only(note: str) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], list[dict[str, str]], list[str], list[dict[str, Any]]]:
            """Запасной выход: только то, что собрал код, без вердикта модели."""
            filled = goods_lots(draft)
            fill_from_catalog(filled, catalog)
            filled, fold_notes = fold_rolls(filled, draft.get("spec_rows"))
            flags_now = list(draft.get("flags") or [])
            header_fields = header_from_draft(draft, filled)
            header_notes = list(draft.get("notes") or []) + [note] + fold_notes
            reconciled = lots_to_rows(filled, flags_now) + fee_rows(draft.get("freights"))
            return filled, flags_now, header_fields, [], header_notes, reconciled

        try:
            lots, flags_now, header_fields, header_changed, header_notes, reconciled = yield from _while_busy(
                "Сборка таблицы", _assemble
            )
        except Exception as exc:  # noqa: BLE001 — всё равно отдаём черновик кода
            note = f"Сборка после модели не вышла ({humanize_exception(exc)}). Показан черновик кода."
            lots, flags_now, header_fields, header_changed, header_notes, reconciled = yield from _while_busy(
                "Сборка таблицы", lambda: _assemble_code_only(note)
            )
            review_dict = {
                "status": "error",
                "model": str(review_dict.get("model") or first_model or ""),
                "model_label": first_chip,
                "raw_text": "",
                "payload": None,
                "error": humanize_exception(exc),
                "image_count": int(review_dict.get("image_count") or 0),
                "excel_attached": False,
                "excel_files": [],
                "meaning": note,
                "header": {},
                "tables": [],
                "totals": None,
                "items": [],
                "context": {"excel": [], "pdfs": []},
            }
            second_used = False

        found = len(reconciled)
        role_type = {
            "invoice": DocType.INVOICE,
            "packing": DocType.PACKING_LIST,
            "specification": DocType.SPECIFICATION,
            "proforma": DocType.INVOICE,
        }
        for doc in draft.get("documents") or []:
            name = str(doc.get("name") or "")
            role = str(doc.get("role") or "")
            message = f"нашлось {found} позиций"
            roles = [r for r in (doc.get("roles") or []) if r]
            if len(roles) > 1:
                message += ", в одном файле: " + " и ".join(_ROLE_RU.get(r, r) for r in roles)
            file_outs.append(
                _make_file_out(
                    filename=name,
                    doc_type=role_type.get(role) or _guess_doc_type(name, None),
                    ocr_confidence=None,
                    parse_status="ok",
                    parse_message=message,
                )
            )
            yield {"event": "file", "filename": name, "status": "ok", "message": message}
        for _path, display in references:
            message = "справочник, в строки поставки не входит"
            file_outs.append(
                _make_file_out(
                    filename=display,
                    doc_type=DocType.CATALOG,
                    ocr_confidence=None,
                    parse_status="ok",
                    parse_message=message,
                )
            )
            yield {"event": "file", "filename": display, "status": "ok", "message": message}
        for flag in flags_now:
            message = _FLAG_RU.get(str(flag), str(flag))
            file_outs.append(
                _make_file_out(
                    filename="сверка",
                    doc_type=None,
                    ocr_confidence=None,
                    parse_status="review",
                    parse_message=message,
                )
            )
            yield {"event": "file", "filename": "сверка", "status": "review", "message": message}
        if review_dict.get("error"):
            model_msg = humanize_message(str(review_dict.get("error")))
            review_dict["error"] = model_msg
            file_outs.append(
                _make_file_out(
                    filename="модель",
                    doc_type=None,
                    ocr_confidence=None,
                    parse_status="review",
                    parse_message=model_msg,
                )
            )
            yield {"event": "file", "filename": "модель", "status": "review", "message": model_msg}

        yield {
            "event": "progress",
            "current": total,
            "total": total,
            "filename": "Сборка таблицы",
            "stage": "reconcile",
            "message": "Сборка таблицы: отдаём результат",
        }

        items = _items_from_rows(reconciled)
        excel_totals = compute_excel_totals(items)
        header_fields = enrich_header_from_goods(header_fields, items)
        # В UI сырой текст и полный payload модели не используются — выкидываем, иначе done-JSON
        # раздувается и прокси рвёт поток («network error»), хотя сборка уже прошла.
        review_dict["raw_text"] = ""
        review_dict["payload"] = None
        review_dict["tables"] = []
        review_dict["excel_totals"] = excel_totals.model_dump()
        stated_totals = _stated_for_review(draft.get("stated") or {})
        review_dict["totals"] = stated_totals
        review_dict["totals_mismatch"] = _totals_differ(excel_totals, stated_totals)
        review_dict["context"] = review_files(
            list(draft.get("documents") or []), reconciled, draft.get("document_tables")
        )
        review_dict["items"] = []
        if review_dict.get("status") == "error":
            review_dict["meaning"] = (
                str(review_dict.get("meaning") or "")
                or "Модель не ответила. В таблице черновик кода."
            )
        elif second_used:
            review_dict["meaning"] = (
                "Обычная модель не собрала таблицу уверенно, смотрела улучшенная."
            )
        elif first_model:
            review_dict["meaning"] = "Вердикт обычной модели."
        review_dict["model_label"] = second_chip if second_used else first_chip
        try:
            model_review = ModelReviewOut.model_validate(review_dict)
        except Exception as exc:  # noqa: BLE001 — ответ модели кривой, таблицу кода всё равно отдаём
            model_review = ModelReviewOut(
                status="error",
                model=str(review_dict.get("model") or ""),
                model_label=str(review_dict.get("model_label") or first_chip or ""),
                error=humanize_exception(exc),
                meaning="Ответ модели не разобран. В таблице черновик кода.",
                image_count=int(review_dict.get("image_count") or 0),
                excel_totals=excel_totals,
                totals_mismatch=_totals_differ(excel_totals, stated_totals),
                context=review_dict.get("context") or {},
            )
            if not any(note for note in header_notes if "не разобран" in note):
                header_notes = list(header_notes) + ["Ответ модели не разобран. Показан черновик кода."]

        warning_count = sum(
            len([err for err in item.validation_errors if not err.resolved]) for item in items
        )
        if skipped_count:
            warning_count += skipped_count
        if model_review.totals_mismatch:
            warning_count += 1
        if review_dict.get("status") == "error" or model_review.status == "error":
            warning_count += 1
        status = (
            ShipmentStatus.NEEDS_REVIEW
            if warning_count or skipped_count or not items or model_review.status == "error"
            else ShipmentStatus.READY_TO_EXPORT
        )

        payload = ShipmentCreateResponse(
            id=uuid.uuid4(),
            title=resolve_shipment_title(title, header_fields),
            profile_type=active_profile,
            status=status,
            header_fields=header_fields,
            files=file_outs,
            items=items,
            item_count=len(items),
            warning_count=warning_count,
            skipped_count=skipped_count,
            model_review=model_review,
            header_changes=header_changed,
            header_notes=header_notes,
            verdict_run={
                "first": first_chip,
                "second": second_chip if second_used else "",
                "used_second": second_used,
            },
        )
        yield {"event": "done", **payload.model_dump(mode="json")}


@router.post("/")
async def create_shipment(
    title: str = Form(""),
    profile_type: ProfileType = Form(...),
    files: list[UploadFile] = File(default=[]),
    extra_files: list[UploadFile] = File(default=[]),
    stream: str = Form(default=""),
) -> Any:
    extras = _filter_upload_batch(extra_files)
    catalog_names = {
        Path((upload.filename or "").replace("\\", "/")).name.lower()
        for upload in extras
        if upload.filename
    }
    incoming = _dedupe_uploads(_filter_upload_batch(files) + extras)
    if not incoming:
        raise HTTPException(
            status_code=400,
            detail="Не найдены файлы Excel, PDF или изображений для обработки",
        )
    mixed = mixed_shipment_error([upload.filename or "" for upload in incoming])
    if mixed:
        raise HTTPException(status_code=400, detail=mixed)

    want_stream = str(stream).strip().lower() in {"1", "true", "yes", "on"}
    if not want_stream:
        done: dict[str, Any] | None = None
        for event in _iter_create_events(
            title=title, profile_type=profile_type, incoming=incoming, catalog_names=catalog_names
        ):
            if event.get("event") == "done":
                done = event
        if done is None:
            raise HTTPException(status_code=500, detail="Не удалось получить результат обработки")
        body = {key: value for key, value in done.items() if key != "event"}
        return ShipmentCreateResponse.model_validate(body)

    def event_stream() -> Iterator[bytes]:
        def _safe_line(event: dict[str, Any]) -> bytes:
            # NaN/Inf из модели дают невалидный JSON → JSON.parse в браузере падает.
            text = json.dumps(event, ensure_ascii=False, default=str)
            text = (
                text.replace(": NaN", ": null")
                .replace(": -Infinity", ": null")
                .replace(": Infinity", ": null")
            )
            return (text + "\n").encode("utf-8")

        try:
            for event in _iter_create_events(
                title=title, profile_type=profile_type, incoming=incoming, catalog_names=catalog_names
            ):
                try:
                    yield _safe_line(event)
                except Exception as exc:  # noqa: BLE001 — битый done не должен оставлять клиента без ответа
                    if event.get("event") == "done":
                        slim = {
                            "event": "done",
                            "id": event.get("id"),
                            "title": event.get("title") or title or "shipment",
                            "profile_type": event.get("profile_type") or profile_type,
                            "status": event.get("status") or "needs_review",
                            "header_fields": event.get("header_fields") or {},
                            "header_changes": event.get("header_changes") or [],
                            "header_notes": list(event.get("header_notes") or [])
                            + [f"Ответ обрезан при передаче: {humanize_exception(exc)}"],
                            "files": event.get("files") or [],
                            "items": event.get("items") or [],
                            "item_count": event.get("item_count") or len(event.get("items") or []),
                            "warning_count": (event.get("warning_count") or 0) + 1,
                            "skipped_count": event.get("skipped_count") or 0,
                            "model_review": {
                                "status": "error",
                                "error": humanize_exception(exc),
                                "meaning": "Передача результата сбойнула. Таблица кода ниже.",
                            },
                            "verdict_run": event.get("verdict_run") or {},
                        }
                        yield _safe_line(slim)
                    else:
                        yield _safe_line({"event": "error", "detail": humanize_exception(exc)})
        except HTTPException as exc:
            yield _safe_line({"event": "error", "detail": exc.detail})
        except Exception as exc:
            yield _safe_line({"event": "error", "detail": humanize_exception(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/permits", response_model=PermitSearchOut)
def search_permits() -> PermitSearchOut:
    return PermitSearchOut(
        item_id=None,
        article=None,
        by_database={
            PermitDatabaseSource.DS_RD.value: [],
            PermitDatabaseSource.SS_RD.value: [],
            PermitDatabaseSource.SARMANT_RD.value: [],
        },
    )


@router.post("/recognize")
def recognize_uploads(
    files: list[UploadFile] = File(...),
    title: str = Form("export"),
    export: bool = Form(False),
) -> Response:
    """Parse a kit without the UI. JSON: file messages, header, table. export=1 returns the archive."""
    from app.services.export import export_18233, export_beijing

    # Старый контур (app/parsing + app/transform). Боевой путь — POST /shipments с prepare_transform_code.
    # Включается только явно, чтобы два разбора не жили в боевом пути одновременно.
    if os.getenv("ENABLE_LEGACY_RECOGNIZE", "").strip() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=410, detail="Старый разбор отключён. Используй загрузку поставки.")

    with tempfile.TemporaryDirectory(prefix="recognize_") as tmp:
        tmp_dir = Path(tmp)
        saved: list[tuple[str, str]] = []
        for upload in files:
            path = _write_upload(tmp_dir, upload)
            display = Path((upload.filename or path.name).replace("\\", "/")).name
            saved.append((str(path), display))
        result = transform_paths(saved)
        rows = canonical_to_rows(result.items)
        header = result.header or {}
        resolved = resolve_shipment_title(title, header)
        if export:
            out_dir = tmp_dir / "out"
            out_dir.mkdir()
            if result.profile == "beijing":
                book = export_beijing(rows, out_dir / f"{safe_export_stem(resolved)}.xlsx", header)
                payload_bytes = book.read_bytes()
                media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                filename = book.name
            else:
                paths = export_18233(rows, out_dir, header=header, shipment_title=resolved)
                buf = BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
                    for path in paths:
                        archive.write(path, arcname=path.name)
                payload_bytes = buf.getvalue()
                media = "application/zip"
                filename = f"{safe_export_stem(resolved)}.zip"
            return Response(
                content=payload_bytes,
                media_type=media,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        body = {
            "profile": result.profile,
            "title": resolved,
            "invoice_no": header.get("invoice_no"),
            "header": {k: v for k, v in header.items() if v not in (None, "", [])},
            "files": [
                {"filename": item.filename, "status": item.status, "message": item.message, "role": item.role_summary}
                for item in result.files
            ],
            "warnings": result.warnings,
            "items": rows,
        }
        return Response(content=json.dumps(body, ensure_ascii=False, default=str), media_type="application/json")


def _column_layout(payload: ExportRequest) -> dict[str, Any]:
    raw = payload.column_layout or {}
    return {key: value.model_dump() for key, value in raw.items()}


@router.get("/columns")
def role_table_columns(profile_type: str = "18233", fabric: bool = False) -> dict[str, Any]:
    from app.services.column_layout import site_columns

    return {"roles": site_columns(profile_type, fabric), "fabric": fabric}


@router.post("/columns/attach")
def attach_source_column(payload: AttachColumnRequest) -> dict[str, Any]:
    from app.services.column_layout import attach_column

    items = [item.model_dump(mode="json") for item in payload.items]
    return attach_column(items, payload.source_rows or [], payload.column)


@router.post("/export")
def export_workspace(payload: ExportRequest) -> Response:
    items = [item.model_dump(mode="json") for item in payload.items]
    header = export_header_fields(payload.header_fields or {}, items)
    title = resolve_shipment_title(payload.title, header)
    safe_title = safe_export_stem(title)
    layout = _column_layout(payload)
    with tempfile.TemporaryDirectory(prefix="export_") as tmp:
        out_dir = Path(tmp)
        if payload.profile_type == ProfileType.BEIJING:
            paths = [export_beijing(items, out_dir / f"{safe_title} для ЭД.xlsx", header, column_layout=layout)]
        else:
            if materials_available():
                try:
                    paths = export_18233_from_templates(
                        items,
                        out_dir,
                        header=header,
                        shipment_title=safe_title,
                        column_layout=layout,
                    )
                except Exception:
                    paths = export_18233(
                        items,
                        out_dir,
                        header=header,
                        shipment_title=safe_title,
                        column_layout=layout,
                    )
            else:
                paths = export_18233(
                    items,
                    out_dir,
                    header=header,
                    shipment_title=safe_title,
                    column_layout=layout,
                )
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                zf.write(path, arcname=path.name)
        data = zip_buf.getvalue()
    filename = f"{safe_title}_Excel.zip"
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    if not ascii_name.lower().endswith(".zip"):
        ascii_name = "export.zip"
    from urllib.parse import quote

    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


@router.post("/export/preview")
def export_preview(payload: ExportRequest) -> dict[str, Any]:
    items = [item.model_dump(mode="json") for item in payload.items]
    header = export_header_fields(payload.header_fields or {}, items)
    return build_export_preview(
        items,
        profile_type=payload.profile_type.value,
        header=header,
        shipment_title=resolve_shipment_title(payload.title, header),
        column_layout=_column_layout(payload),
    )
