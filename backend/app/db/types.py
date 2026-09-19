"""Dialect-portable column types (SQLite local / PostgreSQL production)."""

from __future__ import annotations

import enum
from typing import TypeVar

from sqlalchemy import JSON, Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB

JSONType = JSON().with_variant(JSONB(), "postgresql")

E = TypeVar("E", bound=enum.Enum)


def str_enum(enum_cls: type[E], name: str) -> SAEnum:
    """Store enum values as VARCHAR — works on SQLite and PostgreSQL."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
    )
