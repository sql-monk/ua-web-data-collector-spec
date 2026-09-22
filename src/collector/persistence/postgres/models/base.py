"""Declarative base, naming convention і спільні типи колонок.

Правила (картка WP-01A, «Спільні вимоги»):

- усі PK — UUID; UUIDv7 генерує застосунок (`collector.contracts.new_entity_id`);
  `gen_random_uuid()` як DB default — лише в audit/log таблицях;
- усі timestamps — `timestamptz` (UTC);
- enum-колонки — `TEXT` + CHECK зі значеннями shared-enum (`collector.contracts.enums`),
  не PG enum, щоб додавати значення без `ALTER TYPE`;
- JSONB лише для явно перелічених control-полів (`crawl_jobs.args`, `audit_log.before_state/
  after_state`) — жодного domain payload (R-27).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, MetaData, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Спільний metadata усіх таблиць §9.1 (джерело істини для `alembic check`)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        UUID: Uuid(as_uuid=True),
        dict[str, Any]: JSONB,
    }


UuidPk = Annotated[UUID, mapped_column(Uuid(as_uuid=True), primary_key=True)]
"""PK UUID, значення генерує застосунок (UUIDv7)."""

CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
]
UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    ),
]
Revision = Annotated[int, mapped_column(BigInteger, nullable=False, server_default=text("1"))]
"""Optimistic revision versioned-ресурсів; збільшується кожним UPDATE через репозиторій."""


def enum_check(column: str, values: Iterable[str] | type[StrEnum], name: str) -> CheckConstraint:
    """CHECK `column IN (...)` зі значень enum або явного переліку."""
    members = [m.value for m in values] if isinstance(values, type) else list(values)
    rendered = ", ".join(f"'{value}'" for value in members)
    return CheckConstraint(f"{column} IN ({rendered})", name=name)
