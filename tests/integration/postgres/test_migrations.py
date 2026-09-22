"""clean DB → `alembic upgrade head` → `alembic check` → `downgrade` → `upgrade`; партиції;
append-only `audit_log`; відсутність domain JSONB (R-27)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.migrations import (
    check_no_drift,
    current_revision,
    downgrade_to_base,
    upgrade_to_head,
)
from collector.persistence.postgres.models import Base
from collector.persistence.postgres.partitions import ensure_month_partitions
from collector.persistence.postgres.repositories.audit import append_audit

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "sources",
    "source_policy_versions",
    "source_routes",
    "source_cursors",
    "crawl_runs",
    "crawl_jobs",
    "origin_rate_buckets",
    "origin_rate_permits",
    "worker_pools",
    "worker_instances",
    "scale_commands",
    "dead_letters",
    "audit_log",
}
ALLOWED_JSONB = {
    ("crawl_jobs", "args"),
    ("audit_log", "before_state"),
    ("audit_log", "after_state"),
}


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    return names


async def test_upgrade_check_downgrade_cycle_on_clean_database(
    pg_empty_database: PostgresSettings,
) -> None:
    engine = create_async_engine(pg_empty_database.url, poolclass=None)
    try:
        async with engine.connect() as conn:
            assert await current_revision(conn) is None
        async with engine.begin() as conn:
            await upgrade_to_head(conn)
        async with engine.connect() as conn:
            assert await current_revision(conn) == "0001_control_queue"
            assert await check_no_drift(conn) == []
        assert EXPECTED_TABLES <= await _table_names(engine)

        async with engine.begin() as conn:
            await downgrade_to_base(conn)
        async with engine.connect() as conn:
            assert await current_revision(conn) is None
        assert not (EXPECTED_TABLES & await _table_names(engine))

        async with engine.begin() as conn:
            await upgrade_to_head(conn)
        async with engine.connect() as conn:
            assert await check_no_drift(conn) == []
    finally:
        await engine.dispose()


async def test_models_match_card_contracts(pg_engine: AsyncEngine) -> None:
    """Усі PK UUID (крім natural keys role/origin), timestamps timestamptz, JSONB лише allowlist."""
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if column.type.__class__.__name__ == "JSONB":
                assert (table.name, column.name) in ALLOWED_JSONB, f"{table.name}.{column.name}"
            if column.type.__class__.__name__ == "DateTime":
                assert getattr(column.type, "timezone", False), f"{table.name}.{column.name}"
        pk_types = {c.type.__class__.__name__ for c in table.primary_key.columns}
        if table.name in {"worker_pools", "origin_rate_buckets"}:
            assert pk_types == {"String"}
        else:
            assert "Uuid" in pk_types, table.name
    async with pg_engine.connect() as conn:
        indexes = await conn.run_sync(
            lambda c: {i["name"] for t in EXPECTED_TABLES for i in inspect(c).get_indexes(t)}
        )
    assert {
        "ix_crawl_jobs_status_not_before_priority",
        "ix_crawl_jobs_lease_expires_at",
        "ix_origin_rate_permits_origin_lease_expires_at",
        "ix_worker_instances_role_status_last_heartbeat_at",
        "ix_audit_log_created_at",
        "uq_crawl_runs_running_full",
    } <= indexes


async def test_month_partitions_are_created_and_idempotent(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        created = await ensure_month_partitions(conn, months_ahead=2, start=date(2027, 11, 1))
        again = await ensure_month_partitions(conn, months_ahead=2, start=date(2027, 11, 1))
    assert created == ["audit_log_y2027m11", "audit_log_y2027m12", "audit_log_y2028m01"]
    assert again == []


async def test_audit_log_insert_without_partition_fails_clearly(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        with pytest.raises(DBAPIError, match="no partition of relation"):
            await conn.execute(
                text(
                    "INSERT INTO audit_log (created_at, actor, action, resource_type, resource_id)"
                    " VALUES ('2031-01-01T00:00:00Z', 'a', 'b', 'c', 'd')"
                )
            )


async def test_audit_log_is_append_only(pg_session: AsyncSession) -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    async with pg_session.begin():
        entry = await append_audit(
            pg_session,
            actor="operator",
            action="source.pause",
            resource_type="source",
            resource_id="news_ua_example",
            before={"state": "enabled"},
            after={"state": "paused"},
            now=now,
        )
    audit_id = entry.audit_id  # rollback нижче expire-ить ORM-об'єкти
    for statement in (
        "UPDATE audit_log SET actor = 'x' WHERE audit_id = :id",
        "DELETE FROM audit_log WHERE audit_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with pg_session.begin():
                await pg_session.execute(text(statement), {"id": audit_id})
