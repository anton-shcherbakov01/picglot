"""Declarative base and shared column types.

The schema targets PostgreSQL. SQLite is supported for fast unit tests, so every
Postgres-specific type carries a portable variant.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import DateTime, Integer, MetaData, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

#: Explicit naming so Alembic autogenerate produces stable, diffable names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: JSONB on Postgres, plain JSON elsewhere.
JsonType = JSONB().with_variant(JSON(), "sqlite")


class UtcDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend.

    Postgres round-trips ``timestamptz`` correctly; SQLite (used for fast unit
    tests) silently drops the offset and hands back naive values, which then
    explode when compared against ``datetime.now(UTC)``. Normalising in the type
    keeps every comparison in the codebase safe without per-call-site guards.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value

    def process_result_value(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value


class Base(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JsonType,
        list[Any]: JsonType,
    }

    def as_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        exclude = exclude or set()
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in exclude
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier!r}>"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def id_column(prefix: str) -> Mapped[str]:
    from picglot.core.ids import prefixed_id

    return mapped_column(
        String(40),
        primary_key=True,
        default=lambda: prefixed_id(prefix),
    )


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class VersionMixin:
    """Optimistic concurrency for editor writes."""

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    # Replaced per model where optimistic locking is actually wired up.
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": None}
