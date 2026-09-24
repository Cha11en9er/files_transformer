"""Shipment REST API — Word spec §7 operator workspace."""

from __future__ import annotations

import hashlib
import json
import queue
import re
import shutil
import tempfile
import threading
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
from app.parsing.header_extract import enrich_header_from_goods, export_header_fields, extract_header_fields, merge_header_fields
from app.parsing.pdf_extractor import sniff_kind
from app.parsing.pipeline import parse_upload
from app.parsing.schemas import ParsedDocument
from app.parsing.user_messages import humanize_exception, humanize_message
from app.schemas.api import (
    ExportRequest,
    FileOut,
    ItemOut,
    ModelReviewOut,
    PermitSearchOut,
    ShipmentCreateResponse,
    ValidationErrorOut,
)
from app.services.opencode_review import compact_parser_snapshot, review_with_opencode, to_jsonable
from app.services.pdf_pages import collect_vision_images
from app.services.scan_reconcile import apply_scan_review, compute_excel_totals
from app.services.catalog import CatalogIndex
from app.services.export import build_export_preview, export_18233, export_beijing
from app.services.export_18233_templates import export_18233_from_templates
from app.services.export_style import resolve_shipment_title, safe_export_stem
from app.services.materials_18233 import kit_sources, materials_available
from app.services.profile_18233 import parse_bundle, reconcile_18233
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
        parsed_docs = []
        catalog: CatalogIndex | None = None
        file_outs: list[FileOut] = []
        usable_paths: list[Path] = []
        saved_paths: list[Path] = []
        skipped_count = 0
        header_fields: dict[str, Any] = {}
        active_profile = profile_type
        total = len(incoming)
        yield {"event": "start", "total": total}

        saved: list[tuple[str, str]] = []
        for index, upload in enumerate(incoming, start=1):
            path = _write_upload(tmp_dir, upload)
            saved_paths.append(path)
            display_name = Path((upload.filename or path.name).replace("\\", "/")).name
            saved.append((str(path), display_name))
            yield {"event": "progress", "current": index, "total": total, "filename": display_name, "stage": "parse"}

        yield {"event": "progress", "current": total, "total": total, "filename": "сверка позиций", "stage": "reconcile"}

        # Universal transformer: read every file, map columns, merge by article.
        # Profile is auto-detected from the goods; parsing itself is profile-agnostic.
        result = transform_paths(saved, catalog_names=catalog_names)
        for outcome in result.files:
            if outcome.status == "skipped":
                skipped_count += 1
            else:
                match = next((p for p in saved_paths if p.name == outcome.filename), None)
                if match is not None:
                    usable_paths.append(match)
            file_outs.append(
                _make_file_out(
                    filename=outcome.filename,
                    doc_type=_guess_doc_type(outcome.filename, None),
                    ocr_confidence=None,
                    parse_status=outcome.status,
                    parse_message=outcome.message,
                )
            )
            yield {
                "event": "file",
                "filename": outcome.filename,
                "status": outcome.status,
                "message": outcome.message,
            }

        for warning in result.warnings:
            file_outs.append(
                _make_file_out(
                    filename="сверка",
                    doc_type=None,
                    ocr_confidence=None,
                    parse_status="review",
                    parse_message=warning,
                )
            )
            yield {"event": "file", "filename": "сверка", "status": "review", "message": warning}

        reconciled: list[dict[str, Any]] = canonical_to_rows(result.items)
        parsed_docs = result.sources
        # Export layout is the profile the operator picked. Auto-detect only
        # fills result.profile for diagnostics; it must not switch 18233 <-> Beijing.

        items = _items_from_rows(reconciled)
        excel_totals = compute_excel_totals(items)
        header_fields = merge_header_fields(result.header or {}, header_fields)
        header_fields = enrich_header_from_goods(header_fields, items)

        model_names: list[str] = []
        pdf_names: list[str] = []
        for path in usable_paths:
            suffix = path.suffix.lower()
            if suffix in {".xlsx", ".xls", ".xlsm"}:
                if "сводная" in path.name.lower() or "справочник" in path.name.lower():
                    continue
                model_names.append(path.name)
            elif suffix == ".pdf":
                pdf_names.append(path.name)
        if model_names:
            model_label = ", ".join(model_names)
            model_message = "Модель читает Excel: " + model_label
        elif pdf_names:
            model_label = ", ".join(pdf_names)
            model_message = "Модель читает PDF: " + model_label
        else:
            model_label = "собранные таблицы"
            model_message = "Модель проверяет собранные таблицы"
        yield {
            "event": "progress",
            "current": total,
            "total": total,
            "filename": model_label,
            "stage": "model",
            "message": model_message,
        }
        vision_dir = tmp_dir / "vision"
        try:
            vision_pages = collect_vision_images(saved_paths, vision_dir)
        except Exception:
            vision_pages = []
        if vision_pages:
            seen_pdf: list[str] = []
            for page in vision_pages:
                label = f"{page.source_name}" + (f" стр. {page.page}" if page.page else "")
                if label not in seen_pdf:
                    seen_pdf.append(label)
            yield {
                "event": "progress",
                "current": total,
                "total": total,
                "filename": ", ".join(seen_pdf),
                "stage": "model",
                "message": "Модель смотрит страницы: " + ", ".join(seen_pdf),
            }
        yield {
            "event": "progress",
            "current": total,
            "total": total,
            "filename": (model_names + [p.source_name for p in vision_pages])[0] if (model_names or vision_pages) else "модель",
            "stage": "model",
            "message": "Модель анализирует файлы, ожидание ответа",
        }
        snapshot = compact_parser_snapshot(
            title=title,
            profile_type=active_profile.value,
            files=[f.model_dump(mode="json") for f in file_outs],
            header_fields=header_fields,
            items=[item.model_dump(mode="json") for item in items],
            parsed_docs=parsed_docs,
            pages=vision_pages,
            excel_totals=excel_totals.model_dump(),
        )
        # Keep the NDJSON stream alive while OpenCode thinks — silent gaps
        # of 1–3 minutes get killed by proxies/browsers as "network error".
        review_box: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def _call_model() -> None:
            try:
                review_box.put(
                    (
                        "ok",
                        review_with_opencode(
                            snapshot=snapshot,
                            pages=vision_pages,
                            excel_paths=usable_paths,
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — surface to stream consumer
                review_box.put(("err", exc))

        worker = threading.Thread(target=_call_model, name="opencode-review", daemon=True)
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
                    "filename": "модель",
                    "stage": "model",
                    "message": f"Модель всё ещё отвечает ({waited} с)",
                }
        review_status, review_payload = review_box.get()
        if review_status == "err":
            raise review_payload
        review_dict = review_payload
        review_dict["excel_totals"] = excel_totals.model_dump()
        if review_dict.get("status") == "ok":
            review_dict = apply_scan_review(items, review_dict)
            header_fields = merge_header_fields(header_fields, review_dict.get("header") or {})
            header_fields = enrich_header_from_goods(header_fields, items)
        review_dict["context"] = snapshot.get("context") or {"excel": [], "pdfs": []}
        model_review = ModelReviewOut.model_validate(review_dict)

        warning_count = sum(
            len([err for err in item.validation_errors if not err.resolved]) for item in items
        )
        if skipped_count:
            warning_count += skipped_count
        if model_review.totals_mismatch:
            warning_count += 1
        status = (
            ShipmentStatus.NEEDS_REVIEW
            if warning_count or skipped_count or not items
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
        try:
            for event in _iter_create_events(
                title=title, profile_type=profile_type, incoming=incoming, catalog_names=catalog_names
            ):
                yield (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        except HTTPException as exc:
            yield (
                json.dumps({"event": "error", "detail": exc.detail}, ensure_ascii=False) + "\n"
            ).encode("utf-8")
        except Exception as exc:
            yield (
                json.dumps(
                    {"event": "error", "detail": humanize_exception(exc)},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")

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


@router.post("/export")
def export_workspace(payload: ExportRequest) -> Response:
    items = [item.model_dump(mode="json") for item in payload.items]
    header = export_header_fields(payload.header_fields or {}, items)
    title = resolve_shipment_title(payload.title, header)
    safe_title = safe_export_stem(title)
    with tempfile.TemporaryDirectory(prefix="export_") as tmp:
        out_dir = Path(tmp)
        if payload.profile_type == ProfileType.BEIJING:
            paths = [export_beijing(items, out_dir / f"{safe_title} для ЭД.xlsx", header)]
        else:
            if materials_available():
                try:
                    paths = export_18233_from_templates(
                        items,
                        out_dir,
                        header=header,
                        shipment_title=safe_title,
                    )
                except Exception:
                    paths = export_18233(
                        items,
                        out_dir,
                        header=header,
                        shipment_title=safe_title,
                    )
            else:
                paths = export_18233(
                    items,
                    out_dir,
                    header=header,
                    shipment_title=safe_title,
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
    )
