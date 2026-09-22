"""Операції CLI `collector db migrate` / `collector db roles` (тонкий шар над engine).

Кожна операція відкриває власний engine на DSN з `PostgresSettings` і закриває його; для
тестів/інших викликачів — приймає готовий `AsyncEngine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.engine import create_engine
from collector.persistence.postgres.migrations import (
    check_no_drift,
    current_revision,
    upgrade_to_head,
)
from collector.persistence.postgres.partitions import ensure_month_partitions
from collector.persistence.postgres.roles import apply_roles


@dataclass(frozen=True, slots=True)
class MigrateResult:
    revision_before: str | None
    revision_after: str | None
    partitions_created: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)


async def migrate_database(
    settings: PostgresSettings,
    *,
    check_only: bool = False,
    partitions_months_ahead: int = 3,
    engine: AsyncEngine | None = None,
) -> MigrateResult:
    """`alembic upgrade head` (+ місячні партиції) або лише `alembic check` (`check_only`).

    Transaction boundary: upgrade + партиції — одна транзакція (DDL у PostgreSQL
    транзакційний); check — окреме з'єднання, транзакція якого не комітиться.

    `check_only` **нічого не лишає у схемі, але не є read-only за правами** (L-4 код-рев'ю):
    `alembic check` конфігурує `MigrationContext`, який на порожній БД створює
    `alembic_version` (усе відкочується разом із з'єднанням). Тому команду треба запускати тією
    самою роллю, що й міграції (`CREATE` на схемі), а не моніторинговою read-only роллю.
    """
    own_engine = engine is None
    eng = engine or create_engine(
        settings, pool_size=1, max_overflow=0, application_name="collector-migrate"
    )
    try:
        async with eng.connect() as conn:
            before = await current_revision(conn)
        if check_only:
            async with eng.connect() as conn:
                drift = await check_no_drift(conn)
            return MigrateResult(revision_before=before, revision_after=before, drift=drift)
        async with eng.begin() as conn:
            await upgrade_to_head(conn)
            created = await ensure_month_partitions(conn, months_ahead=partitions_months_ahead)
        async with eng.connect() as conn:
            after = await current_revision(conn)
            drift = await check_no_drift(conn)
        return MigrateResult(
            revision_before=before, revision_after=after, partitions_created=created, drift=drift
        )
    finally:
        if own_engine:
            await eng.dispose()


async def apply_database_roles(
    settings: PostgresSettings,
    *,
    sql_path: Path | None = None,
    engine: AsyncEngine | None = None,
) -> None:
    """Ролі + GRANT (`sql/roles.sql`) в одній транзакції."""
    own_engine = engine is None
    eng = engine or create_engine(
        settings, pool_size=1, max_overflow=0, application_name="collector-roles"
    )
    try:
        async with eng.begin() as conn:
            await apply_roles(conn, sql_path=sql_path)
    finally:
        if own_engine:
            await eng.dispose()
