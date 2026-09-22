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
    head_revision,
    upgrade_to_head,
)
from collector.persistence.postgres.models import Base
from collector.persistence.postgres.partitions import (
    default_partition_name,
    default_partition_row_count,
    ensure_month_partitions,
)
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
            assert await current_revision(conn) == head_revision()
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


async def test_partitioned_table_without_default_still_fails_clearly(
    pg_engine: AsyncEngine,
) -> None:
    """Контракт «зрозуміла помилка замість auto-create» лишається для таблиць **без** DEFAULT
    (PR2: `fetches`, `raw_objects`, `change_events`, `outbox_events` — рішення по кожній
    приймається окремо). Перевірено на тимчасовій партиційованій таблиці, щоб не залежати від
    наявності DEFAULT в `audit_log` (M-5)."""
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE probe_partitioned (created_at timestamptz NOT NULL, v int) "
                "PARTITION BY RANGE (created_at)"
            )
        )
        with pytest.raises(DBAPIError, match="no partition of relation"):
            await conn.execute(
                text("INSERT INTO probe_partitioned VALUES ('2031-01-01T00:00:00+00', 1)")
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


@pytest.mark.parametrize("session_timezone", ["UTC", "Europe/Kyiv", "America/Los_Angeles"])
async def test_month_partition_bounds_are_utc_regardless_of_session_timezone(
    pg_engine: AsyncEngine, session_timezone: str
) -> None:
    """M-1: межі партицій не залежать від `TimeZone` сесії, що виконує DDL.

    Date-літерали інтерпретувались у часовому поясі сесії, тож партиції з різних сесій або
    перекривались (`would overlap` ламав усю `collector db migrate`), або лишали діру в
    кілька годин, у яку не можна вставити рядок.
    """
    async with pg_engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL TIME ZONE '{session_timezone}'"))
        await ensure_month_partitions(conn, months_ahead=1, start=date(2033, 6, 1))
    async with pg_engine.connect() as conn:
        bounds = dict(
            (
                await conn.execute(
                    text(
                        "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) FROM pg_class c "
                        "JOIN pg_inherits i ON i.inhrelid = c.oid "
                        "WHERE i.inhparent = 'audit_log'::regclass AND c.relname LIKE '%y2033%'"
                    )
                )
            ).all()
        )
    assert (
        "FROM ('2033-06-01 00:00:00+00') TO ('2033-07-01 00:00:00+00')"
        in bounds["audit_log_y2033m06"]
    )
    assert (
        "FROM ('2033-07-01 00:00:00+00') TO ('2033-08-01 00:00:00+00')"
        in bounds["audit_log_y2033m07"]
    )


async def test_partitions_created_from_different_timezones_neither_overlap_nor_leave_gaps(
    pg_engine: AsyncEngine,
) -> None:
    """M-1, обидва відтворені рев'юером сценарії: сусідні місяці з різних сесій."""
    async with pg_engine.begin() as conn:
        await conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        await ensure_month_partitions(conn, months_ahead=0, start=date(2034, 3, 1))
    async with pg_engine.begin() as conn:
        # Раніше саме тут падало `would overlap partition "audit_log_y2034m03"`.
        await conn.execute(text("SET LOCAL TIME ZONE 'Europe/Kyiv'"))
        await ensure_month_partitions(conn, months_ahead=0, start=date(2034, 4, 1))

    # Межа місяця у UTC: рядок 2034-03-31T22:00Z має потрапити саме у березневу партицію,
    # а 2034-04-01T00:00Z — у квітневу; діри між ними немає.
    async with pg_engine.begin() as conn:
        for moment, expected in (
            (datetime(2034, 3, 31, 22, 0, tzinfo=UTC), "audit_log_y2034m03"),
            (datetime(2034, 4, 1, 0, 0, tzinfo=UTC), "audit_log_y2034m04"),
        ):
            landed = await conn.scalar(
                text(
                    "INSERT INTO audit_log (created_at, actor, action, resource_type, resource_id)"
                    " VALUES (:moment, 'a', 'b', 'c', 'd') RETURNING tableoid::regclass::text"
                ),
                {"moment": moment},
            )
            assert landed == expected, moment


async def test_audit_log_default_partition_accepts_rows_without_monthly_partition(
    pg_engine: AsyncEngine,
) -> None:
    """M-5: пропущене обслуговування не зупиняє audited дії — рядок іде в DEFAULT-партицію."""
    async with pg_engine.begin() as conn:
        landed = await conn.scalar(
            text(
                "INSERT INTO audit_log (created_at, actor, action, resource_type, resource_id)"
                " VALUES ('2039-01-01T00:00:00+00', 'a', 'b', 'c', 'd')"
                " RETURNING tableoid::regclass::text"
            )
        )
        assert landed == default_partition_name("audit_log")
        assert await default_partition_row_count(conn, "audit_log") == 1


async def test_fresh_upgrade_head_without_maintenance_can_write_audit(
    pg_empty_database: PostgresSettings,
) -> None:
    """M-5, сценарій рев'ю: чистий `alembic upgrade head` (перший крок CI) без жодного
    `db migrate` — `append_audit` має працювати, інакше control plane стає read-only."""
    engine = create_async_engine(pg_empty_database.url, poolclass=None)
    try:
        async with engine.begin() as conn:
            await upgrade_to_head(conn)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO audit_log (created_at, actor, action, resource_type, resource_id)"
                    " VALUES (now(), 'operator', 'worker_pool.scale', 'worker_pool', 'fetch')"
                )
            )
            assert await conn.scalar(text("SELECT count(*) FROM audit_log")) == 1
    finally:
        await engine.dispose()
