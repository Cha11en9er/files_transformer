"""Shared site-wide shipment history API."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.api.deps import current_user
from app.schemas.api import (
    ExportRequest,
    HistoryDeleteFileRequest,
    HistoryRenameRequest,
    HistorySaveRequest,
)
from app.services import auth_store, history_store
from app.services.history_store import HistoryConflictError

router = APIRouter(prefix="/api/v1/history", tags=["history"])


def _workspace_blob(payload: HistorySaveRequest) -> dict[str, Any]:
    items = [item.model_dump(mode="json") for item in payload.items]
    return {
        "id": payload.history_id,
        "history_id": payload.history_id,
        "title": payload.title,
        "profile_type": payload.profile_type.value if hasattr(payload.profile_type, "value") else str(payload.profile_type),
        "status": payload.status,
        "header_fields": payload.header_fields or {},
        "header_changes": payload.header_changes or [],
        "header_notes": payload.header_notes or [],
        "files": payload.files or [],
        "items": items,
        "item_count": payload.item_count if payload.item_count is not None else len(items),
        "warning_count": payload.warning_count if payload.warning_count is not None else 0,
        "skipped_count": payload.skipped_count if payload.skipped_count is not None else 0,
        "model_review": payload.model_review,
        "verdict_run": payload.verdict_run or {},
        "column_layout": payload.column_layout or {},
        "recognition": payload.recognition or {},
    }


def _user(request: Request) -> dict[str, Any]:
    return current_user(request)


def _owned(user: dict[str, Any], item: dict[str, Any] | None) -> dict[str, Any]:
    if item is None or not auth_store.can_see_shipment(user, item):
        raise HTTPException(status_code=404, detail="Поставка в истории не найдена")
    return item


def _audit_shipment(user: dict[str, Any], action: str, item: dict[str, Any], detail: str = "") -> None:
    auth_store.record_audit(
        user,
        action,
        shipment_id=str(item.get("id") or ""),
        title=str(item.get("title") or ""),
        invoice_no=str(item.get("invoice_no") or ""),
        detail=detail,
    )


@router.get("/")
def list_history(request: Request) -> dict[str, Any]:
    user = _user(request)
    owner = None if user.get("role") == "admin" else str(user.get("id") or "")
    items = history_store.list_shipments(owner_user_id=owner)
    total = sum(int(item.get("total_bytes") or 0) for item in items)
    return {
        "items": items,
        "total_bytes": total,
        "count": len(items),
    }


@router.get("/{history_id}")
def get_history(history_id: str, request: Request) -> dict[str, Any]:
    user = _user(request)
    return _owned(user, history_store.get_shipment(history_id))


@router.patch("/{history_id}")
def rename_history(history_id: str, payload: HistoryRenameRequest, request: Request) -> dict[str, Any]:
    user = _user(request)
    _owned(user, history_store.get_shipment(history_id))
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Укажи название поставки")
    item = history_store.rename_shipment(history_id, title)
    if item is None:
        raise HTTPException(status_code=404, detail="Поставка в истории не найдена")
    return item


@router.post("/")
def save_history(payload: HistorySaveRequest, request: Request) -> dict[str, Any]:
    """Save workspace and always write an Excel ZIP into history exports."""
    hid = (payload.history_id or "").strip()
    if not hid:
        raise HTTPException(status_code=400, detail="Нет history_id")
    user = _user(request)
    _owned(user, history_store.get_shipment(hid))
    try:
        item = history_store.save_workspace(
            hid,
            _workspace_blob(payload),
            title=payload.title,
            base_updated_at=payload.base_updated_at,
            force=bool(payload.force),
        )
    except HistoryConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "history_conflict",
                "message": "Эту поставку уже сохранил кто-то другой. Обнови из истории или перезапиши своей версией.",
                "updated_at": exc.updated_at,
                "title": exc.title,
                "id": exc.shipment_id,
            },
        ) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Поставка в истории не найдена")
    # «Сохранить в историю» раньше писало только workspace.json — архивы оставались Пусто.
    # ZIP пишем здесь же, без скачивания: тот же build_export_zip, что у кнопки Excel.
    from app.api.routes.shipments import build_export_zip

    export_payload = ExportRequest(
        title=payload.title,
        profile_type=payload.profile_type,
        header_fields=payload.header_fields or {},
        items=payload.items,
        column_layout=payload.column_layout or {},
        recognition=payload.recognition or {},
    )
    data, filename = build_export_zip(export_payload)
    stored = history_store.store_export_zip(hid, data, filename=filename)
    if stored is None:
        raise HTTPException(status_code=500, detail="Workspace сохранён, но архив в историю не записался")
    refreshed = history_store.get_shipment(hid)
    if refreshed is not None:
        _audit_shipment(user, "history_saved", refreshed, "сохранил версию")
    return refreshed or item


@router.post("/{history_id}/export")
def export_history(history_id: str, payload: ExportRequest, request: Request) -> Response:
    # Lazy import: shipments also imports history_store; avoid cycle at module load.
    from app.api.routes.shipments import _zip_response, build_export_zip

    hid = (history_id or "").strip()
    user = _user(request)
    existing = _owned(user, history_store.get_shipment(hid))
    # Keep workspace in sync with what was exported.
    workspace = {
        "id": hid,
        "history_id": hid,
        "title": payload.title,
        "profile_type": payload.profile_type.value if hasattr(payload.profile_type, "value") else str(payload.profile_type),
        "header_fields": payload.header_fields or {},
        "items": [item.model_dump(mode="json") for item in payload.items],
        "column_layout": {
            key: (value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
            for key, value in (payload.column_layout or {}).items()
        },
        "recognition": payload.recognition or {},
    }
    history_store.save_workspace(hid, workspace, title=payload.title)
    data, filename = build_export_zip(payload)
    stored = history_store.store_export_zip(hid, data, filename=filename)
    if stored is None:
        raise HTTPException(status_code=500, detail="Не удалось сохранить архив в историю")
    response = _zip_response(data, filename)
    response.headers["X-History-Export"] = stored["name"]
    _audit_shipment(user, "export_zip", existing, stored["name"])
    return response


@router.delete("/{history_id}")
def delete_history(history_id: str, request: Request) -> dict[str, str]:
    user = _user(request)
    item = _owned(user, history_store.get_shipment(history_id))
    if not history_store.delete_shipment(history_id):
        raise HTTPException(status_code=404, detail="Поставка в истории не найдена")
    _audit_shipment(user, "shipment_deleted", item, "удалил поставку из истории")
    return {"status": "deleted", "id": history_id}


@router.delete("/{history_id}/files")
def delete_history_file(history_id: str, payload: HistoryDeleteFileRequest, request: Request) -> dict[str, Any]:
    user = _user(request)
    item = _owned(user, history_store.get_shipment(history_id))
    if not history_store.delete_file(history_id, payload.kind, payload.name):
        raise HTTPException(status_code=404, detail="Файл не найден")
    _audit_shipment(user, "file_deleted", item, f"{payload.kind}/{payload.name}")
    fresh = history_store.get_shipment(history_id)
    return fresh or {"status": "deleted"}


@router.get("/{history_id}/files/{kind}/{name}")
def download_history_file(history_id: str, kind: str, name: str, request: Request) -> FileResponse:
    user = _user(request)
    item = _owned(user, history_store.get_shipment(history_id))
    path = history_store.resolve_file(history_id, kind, name)
    if path is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    if kind == "exports":
        _audit_shipment(user, "export_zip", item, path.name)
    media = "application/zip" if path.suffix.lower() == ".zip" else "application/octet-stream"
    ascii_name = path.name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(path.name)}"
    return FileResponse(
        path,
        media_type=media,
        headers={"Content-Disposition": disposition, "Cache-Control": "private, no-store"},
    )
