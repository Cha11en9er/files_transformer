from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import DocType


class ParsedLine(BaseModel):
    row_index: int
    sheet_name: str | None = None
    article: str | None = None
    model: str | None = None
    normalized_article: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    ocr_confidence: float | None = None


class ParsedDocument(BaseModel):
    filename: str
    file_path: str
    mime_hint: str | None = None
    doc_type: DocType | None = None
    classification_score: float = 0.0
    classification_hits: list[str] = Field(default_factory=list)
    ocr_used: bool = False
    ocr_confidence: float | None = None
    text_preview: str = ""
    sheets: list[str] = Field(default_factory=list)
    lines: list[ParsedLine] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    doc_type: DocType | None = None
    score: float = 0.0
    hits: list[str] = Field(default_factory=list)
    scores_by_type: dict[str, float] = Field(default_factory=dict)


ExtractionKind = Literal["excel", "pdf", "image", "unknown"]
