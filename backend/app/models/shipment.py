from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONType, str_enum
from app.models.enums import ProfileType, ShipmentStatus

if TYPE_CHECKING:
    from app.models.item import Item
    from app.models.uploaded_file import UploadedFile


class Shipment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One customs processing job (batch of supply documents)."""

    __tablename__ = "shipments"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_type: Mapped[ProfileType] = mapped_column(
        str_enum(ProfileType, "profile_type"),
        nullable=False,
    )
    status: Mapped[ShipmentStatus] = mapped_column(
        str_enum(ShipmentStatus, "shipment_status"),
        nullable=False,
        default=ShipmentStatus.DRAFT,
        server_default=ShipmentStatus.DRAFT.value,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONType,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    files: Mapped[list[UploadedFile]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    items: Mapped[list[Item]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Shipment id={self.id} title={self.title!r} profile={self.profile_type}>"
