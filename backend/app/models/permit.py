from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import str_enum
from app.models.enums import PermitDatabaseSource, PermitStatus


class PermitDatabase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permit_databases"
    __table_args__ = (
        Index(
            "ix_permit_databases_tnved_manufacturer_brand",
            "tnved_code",
            "manufacturer",
            "brand",
        ),
        UniqueConstraint(
            "database_source",
            "doc_number",
            "tnved_code",
            name="uq_permit_databases_source_doc_tnved",
        ),
    )

    database_source: Mapped[PermitDatabaseSource] = mapped_column(
        str_enum(PermitDatabaseSource, "permit_database_source"),
        nullable=False,
        index=True,
    )
    tnved_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    doc_number: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[PermitStatus] = mapped_column(
        str_enum(PermitStatus, "permit_status"),
        nullable=False,
        default=PermitStatus.UNKNOWN,
        server_default=PermitStatus.UNKNOWN.value,
    )
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PermitDatabase source={self.database_source} "
            f"doc={self.doc_number!r} tnved={self.tnved_code!r}>"
        )
