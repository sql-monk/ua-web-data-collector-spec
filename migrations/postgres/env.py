"""Alembic env для PostgreSQL (WP-01A): async engine (asyncpg) або спільне з'єднання викликача.

Режими:

- CLI `alembic upgrade head` / `alembic check`: DSN з env `COLLECTOR_POSTGRES_DSN`
  (`COLLECTOR_POSTGRES_DSN_FILE`), власний async engine, `asyncio.run`;
- програмний (`collector.persistence.postgres.migrations`): викликач передає sync-з'єднання
  у `config.attributes["connection"]` (через `AsyncConnection.run_sync`) — межа транзакції
  належить викликачу.

Autogenerate ігнорує child-партиції (`audit_log_y2026m09`, ...) — їх створює
`partitions.ensure_month_partitions`, а не міграції; `compare_type=True` для точних типів.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.models import Base
from collector.persistence.postgres.partitions import is_partition_child_name

config = context.config
target_metadata = Base.metadata

# Логування з alembic.ini лише для CLI-режиму; програмний виклик (shared connection)
# не переналаштовує logging застосунку (structlog, collector.core.logging).
if config.config_file_name is not None and config.attributes.get("connection") is None:
    fileConfig(config.config_file_name)


def include_name(
    name: str | None, type_: str, parent_names: dict[str, Any]
) -> bool:  # pragma: no cover - викликається Alembic
    if type_ == "table" and name is not None and is_partition_child_name(name):
        return False
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        compare_type=True,
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    """`alembic upgrade --sql`: DDL у stdout без з'єднання."""
    settings = PostgresSettings.from_env()
    context.configure(
        url=settings.url,
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_with_connection(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_async() -> None:
    settings = PostgresSettings.from_env()
    engine = create_async_engine(settings.url, poolclass=None)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations_with_connection)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    shared: Connection | None = config.attributes.get("connection")
    if shared is not None:
        run_migrations_with_connection(shared)
        return
    asyncio.run(run_migrations_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
