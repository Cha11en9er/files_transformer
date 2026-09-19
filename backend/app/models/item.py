from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONType

if TYPE_CHECKING:
    from app.models.shipment import Shipment
    from app.models.validation_error import ValidationError


class Item(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reconciled line position, keyed strictly by normalized article/model."""

    __tablename__ = "items"
    __table_args__ = (
        Index("ix_items_shipment_id_normalized_article", "shipment_id", "normalized_article"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    article: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_article: Mapped[str] = mapped_column(String(255), nullable=False)
    commercial_data: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default="{}"
    )
    packing_data: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default="{}"
    )
    customs_data: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default="{}"
    )
    source_traces: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default="{}"
    )

    shipment: Mapped[Shipment] = relationship(back_populates="items")
    validation_errors: Mapped[list[ValidationError]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Item id={self.id} article={self.normalized_article!r}>"
