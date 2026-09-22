"""Ролі БД §13: `collector_api_ro` INSERT → permission denied; runtime-ролі без DDL/UPDATE на
audit_log; `collector_translation` не пише у `crawl_jobs`; ролі створені ідемпотентно."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from collector.persistence.postgres.roles import ROLE_NAMES, apply_roles

pytestmark = pytest.mark.integration

INSERT_JOB = (
    "INSERT INTO crawl_jobs (job_id, job_type, idempotency_key) "
    "VALUES (gen_random_uuid(), 'fetch', 'k-' || gen_random_uuid()::text)"
)


async def _as_role(engine: AsyncEngine, role: str, statement: str) -> None:
    """Виконує statement під `SET ROLE` (superuser → роль, без login/паролів)."""
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL ROLE {role}"))
        await conn.execute(text(statement))


async def test_all_roles_exist_and_apply_is_idempotent(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await apply_roles(conn)
        rows = await conn.execute(
            text("SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'collector\\_%'")
        )
        roles = {name: can_login for name, can_login in rows}
    assert set(ROLE_NAMES) <= set(roles)
    assert not any(roles[name] for name in ROLE_NAMES), "ролі мають бути NOLOGIN"


@pytest.mark.parametrize("role", ["collector_api_ro", "collector_export_ro"])
async def test_read_only_roles_cannot_insert(pg_engine: AsyncEngine, role: str) -> None:
    await _as_role(pg_engine, role, "SELECT count(*) FROM crawl_jobs")
    with pytest.raises(DBAPIError, match="permission denied"):
        await _as_role(pg_engine, role, INSERT_JOB)
    with pytest.raises(DBAPIError, match="permission denied"):
        await _as_role(pg_engine, role, "SELECT * FROM alembic_version")


async def test_fetcher_can_enqueue_but_translation_cannot(pg_engine: AsyncEngine) -> None:
    await _as_role(pg_engine, "collector_fetcher", INSERT_JOB)
    with pytest.raises(DBAPIError, match="permission denied"):
        await _as_role(pg_engine, "collector_translation", INSERT_JOB)
    with pytest.raises(DBAPIError, match="permission denied"):
        await _as_role(pg_engine, "collector_projector", INSERT_JOB)


async def test_runtime_roles_have_no_ddl_and_no_audit_update(pg_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="permission denied|must be owner"):
        await _as_role(pg_engine, "collector_scheduler", "ALTER TABLE crawl_jobs ADD COLUMN x int")
    await _as_role(
        pg_engine,
        "collector_scheduler",
        "INSERT INTO audit_log (created_at, actor, action, resource_type, resource_id) "
        "VALUES ('2026-09-22T12:00:00Z', 'a', 'b', 'c', 'd')",
    )
    with pytest.raises(DBAPIError, match="permission denied"):
        await _as_role(pg_engine, "collector_scheduler", "UPDATE audit_log SET actor = 'z'")
    with pytest.raises(DBAPIError, match="permission denied"):
        await _as_role(pg_engine, "collector_fetcher", "DELETE FROM crawl_jobs")
