from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import str_enum
from app.models.enums import DocType

if TYPE_CHECKING:
    from app.models.shipment import Shipment


class UploadedFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "files"

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[DocType | None] = mapped_column(
        str_enum(DocType, "doc_type"),
        nullable=True,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parse_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    shipment: Mapped[Shipment] = relationship(back_populates="files")

    def __repr__(self) -> str:
        return f"<UploadedFile id={self.id} filename={self.filename!r} doc_type={self.doc_type}>"
