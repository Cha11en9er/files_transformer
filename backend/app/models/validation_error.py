from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONType, str_enum
from app.models.enums import ErrorSeverity, ErrorType

if TYPE_CHECKING:
    from app.models.item import Item


class ValidationError(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "validation_errors"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    error_type: Mapped[ErrorType] = mapped_column(
        str_enum(ErrorType, "error_type"),
        nullable=False,
        index=True,
    )
    severity: Mapped[ErrorSeverity] = mapped_column(
        str_enum(ErrorSeverity, "error_severity"),
        nullable=False,
        index=True,
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    item: Mapped[Item] = relationship(back_populates="validation_errors")

    def __repr__(self) -> str:
        return (
            f"<ValidationError id={self.id} field={self.field_name!r} "
            f"type={self.error_type} severity={self.severity}>"
        )
