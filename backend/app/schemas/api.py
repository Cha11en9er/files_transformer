from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import DocType, ErrorSeverity, ErrorType, ProfileType, ShipmentStatus


class HeaderFieldsSchema(BaseModel):
    buyer: str | None = None
    seller: str | None = None
    contract_no: str | None = None
    contract_date: str | None = None
    incoterms: str | None = None
    date: str | None = None
    invoice_no: str | None = None
    invoice_date: str | None = None
    manufacturer: str | None = None
    payment_terms: str | None = None
    delivery_terms: str | None = None
    delivery_date: str | None = None
    warehouse_address: str | None = None
    consignee: str | None = None
    shipper: str | None = None
    subkits: list[str] = Field(default_factory=list)


class FileOut(BaseModel):
    id: uuid.UUID
    filename: str
    doc_type: DocType | None
    ocr_confidence: float | None
    parse_status: str = "ok"
    parse_message: str | None = None

    model_config = {"from_attributes": True}


class ValidationErrorOut(BaseModel):
    id: uuid.UUID | None = None
    field_name: str
    error_type: ErrorType | str
    severity: ErrorSeverity | str
    details: dict[str, Any] | None = None
    message: str | None = None
    resolved: bool = False


class ItemOut(BaseModel):
    id: uuid.UUID
    article: str | None
    model: str | None
    normalized_article: str
    commercial_data: dict[str, Any]
    packing_data: dict[str, Any]
    customs_data: dict[str, Any]
    source_traces: dict[str, Any] = Field(default_factory=dict)
    validation_errors: list[ValidationErrorOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ItemUpdate(BaseModel):
    article: str | None = None
    model: str | None = None
    commercial_data: dict[str, Any] | None = None
    packing_data: dict[str, Any] | None = None
    customs_data: dict[str, Any] | None = None


class ScanTotalsOut(BaseModel):
    qty: float | None = None
    meters: float | None = None
    amount: float | None = None
    rolls: float | None = None
    boxes: float | None = None
    net_weight: float | None = None
    gross_weight: float | None = None
    area: float | None = None
    volume: float | None = None


class ScanTableOut(BaseModel):
    role: str = "ignored"
    page: int | None = None
    source: str | None = None
    why: str | None = None
    columns: dict[str, str] = Field(default_factory=dict)


class ScanItemOut(BaseModel):
    article: str | None = None
    matched_article: str | None = None
    item_id: str | None = None
    qty: float | None = None
    meters: float | None = None
    unit: str | None = None
    rolls: float | None = None
    price: float | None = None
    amount: float | None = None
    net_weight: float | None = None
    gross_weight: float | None = None
    area: float | None = None
    volume: float | None = None
    measurement: str | None = None
    description: str | None = None
    color: str | None = None
    boxes: float | None = None
    verdict: str = "question"
    notes: str | None = None


class ModelReviewOut(BaseModel):
    status: str = "skipped"
    model: str = ""
    raw_text: str = ""
    payload: dict[str, Any] | list[Any] | None = None
    error: str | None = None
    image_count: int = 0
    meaning: str | None = None
    header: dict[str, Any] = Field(default_factory=dict)
    tables: list[ScanTableOut] = Field(default_factory=list)
    totals: ScanTotalsOut | None = None
    excel_totals: ScanTotalsOut | None = None
    totals_mismatch: bool = False
    excel_attached: bool = False
    excel_files: list[str] = Field(default_factory=list)
    items: list[ScanItemOut] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class ShipmentCreateResponse(BaseModel):
    id: uuid.UUID
    title: str
    profile_type: ProfileType
    status: ShipmentStatus
    header_fields: dict[str, Any] = Field(default_factory=dict)
    files: list[FileOut]
    items: list[ItemOut] = Field(default_factory=list)
    item_count: int
    warning_count: int
    skipped_count: int = 0
    model_review: ModelReviewOut | None = None


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    title: str
    profile_type: ProfileType
    status: ShipmentStatus
    header_fields: dict[str, Any]
    files: list[FileOut]
    items: list[ItemOut]
    created_at: datetime | None = None
    model_review: ModelReviewOut | None = None


class ExportRequest(BaseModel):
    title: str = "export"
    profile_type: ProfileType
    header_fields: dict[str, Any] = Field(default_factory=dict)
    items: list[ItemOut] = Field(default_factory=list)


class PermitCandidate(BaseModel):
    id: uuid.UUID
    database_source: str
    tnved_code: str
    manufacturer: str | None
    brand: str | None
    doc_number: str
    status: str
    valid_until: str | None = None


class PermitSearchOut(BaseModel):
    item_id: uuid.UUID | None = None
    article: str | None = None
    by_database: dict[str, list[PermitCandidate]]


class ExportOut(BaseModel):
    files: list[str]
    download_base: str
