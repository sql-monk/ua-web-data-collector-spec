"""Ролі БД §13 через **окремі з'єднання** під login-користувачем кожної ролі.

`SET ROLE` із superuser-сесії перевіряє GRANT, але не доводить, що роль справді може
підключитися й що жодне право не «просочується» через superuser-контекст. Тут для кожної
ролі створюється login-користувач — член групової ролі — і кожен тест працює під ним.

Перевіряється:

- `collector_api_ro`/`collector_export_ro` не мають INSERT/UPDATE/DELETE у **жодній** таблиці
  (і каталог прав, і реально виконаний statement);
- `collector_parser` лише додає рядки в `audit_log` (INSERT), не читає і не змінює журнал;
- жодна runtime-роль не може UPDATE/DELETE `audit_log`;
- `audit_log` UPDATE/DELETE відхиляється тригером навіть для owner (`collector_migrate`);
- `collector_migrate` не згадується у runtime-коді.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.roles import ROLE_NAMES, RUNTIME_ROLES

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
READ_ONLY_ROLES = ("collector_api_ro", "collector_export_ro")
ALL_TABLES = (
    "sources",
    "source_policy_versions",
    "source_routes",
    "source_cursors",
    "crawl_runs",
    "crawl_jobs",
    "dead_letters",
    "origin_rate_buckets",
    "origin_rate_permits",
    "worker_pools",
    "worker_instances",
    "scale_commands",
    "audit_log",
)
AUDIT_INSERT = (
    "INSERT INTO audit_log (created_at, actor, action, resource_type, resource_id) "
    "VALUES ('2026-09-22T12:00:00Z', 'a', 'b', 'c', 'd')"
)
RoleEngine = Callable[[str], Awaitable[AsyncEngine]]


@pytest.fixture
async def role_engine(pg_database: PostgresSettings) -> AsyncIterator[RoleEngine]:
    """`await role_engine("collector_api_ro")` → engine під власним login-користувачем."""
    admin = create_async_engine(pg_database.url, isolation_level="AUTOCOMMIT", poolclass=None)
    password = secrets.token_hex(16)  # одноразовий, лише loopback-контейнер
    suffix = secrets.token_hex(4)
    created: list[str] = []
    engines: list[AsyncEngine] = []

    async def make(role: str) -> AsyncEngine:
        login = f"t_{role}_{suffix}"
        if login not in created:
            async with admin.connect() as conn:
                await conn.execute(
                    text(f"CREATE ROLE {login} LOGIN PASSWORD '{password}' IN ROLE {role}")
                )
            created.append(login)
        engine = create_async_engine(
            pg_database.url.set(username=login, password=password), poolclass=None
        )
        engines.append(engine)
        return engine

    try:
        yield make
    finally:
        for engine in engines:
            await engine.dispose()
        async with admin.connect() as conn:
            for login in created:
                await conn.execute(text(f"DROP ROLE IF EXISTS {login}"))
        await admin.dispose()


async def _privileges(engine: AsyncEngine, table: str) -> dict[str, bool]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT has_table_privilege(:t, 'SELECT') AS s,"
                    " has_table_privilege(:t, 'INSERT') AS i,"
                    " has_table_privilege(:t, 'UPDATE') AS u,"
                    " has_table_privilege(:t, 'DELETE') AS d,"
                    " has_table_privilege(:t, 'TRUNCATE') AS tr"
                ),
                {"t": table},
            )
        ).one()
    return {"select": row.s, "insert": row.i, "update": row.u, "delete": row.d, "truncate": row.tr}


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
async def test_read_only_role_has_no_write_privilege_on_any_table(
    role_engine: RoleEngine, role: str
) -> None:
    engine = await role_engine(role)
    async with engine.connect() as conn:
        assert str(await conn.scalar(text("SELECT current_user"))).startswith(f"t_{role}_")
    for table in ALL_TABLES:
        privileges = await _privileges(engine, table)
        assert privileges["select"] is True, table
        assert privileges["insert"] is False, table
        assert privileges["update"] is False, table
        assert privileges["delete"] is False, table
        assert privileges["truncate"] is False, table


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
async def test_read_only_role_write_statements_are_denied_at_runtime(
    role_engine: RoleEngine, role: str
) -> None:
    engine = await role_engine(role)
    statements = (
        "INSERT INTO crawl_jobs (job_id, job_type, idempotency_key)"
        " VALUES (gen_random_uuid(), 'fetch', 'ro')",
        "UPDATE crawl_jobs SET status = 'succeeded'",
        "DELETE FROM crawl_jobs",
        AUDIT_INSERT,
        "UPDATE worker_pools SET desired_replicas = 99",
        "DELETE FROM origin_rate_permits",
        "TRUNCATE dead_letters",
    )
    for statement in statements:
        with pytest.raises(DBAPIError, match="permission denied|must be owner"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM crawl_jobs")) == 0


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
async def test_read_only_role_cannot_touch_alembic_version_or_ddl(
    role_engine: RoleEngine, role: str
) -> None:
    engine = await role_engine(role)
    for statement in (
        "SELECT * FROM alembic_version",
        "CREATE TABLE tester_probe (x int)",
        "DROP TABLE crawl_jobs",
        "ALTER TABLE crawl_jobs ADD COLUMN probe int",
    ):
        with pytest.raises(DBAPIError, match="permission denied|must be owner"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))


async def test_parser_appends_audit_but_cannot_read_or_rewrite_it_and_keeps_its_queue(
    role_engine: RoleEngine,
) -> None:
    """Parser веде свої jobs у черзі (claim = UPDATE `crawl_jobs`). З PR2 (S-2 пострев'ю PR1)
    bootstrap `upsert_pool` пише audit у своїй транзакції, тому parser має рівно `INSERT` на
    `audit_log` — ні читати журнал, ні змінювати/видаляти записи він не може."""
    engine = await role_engine("collector_parser")
    assert await _privileges(engine, "audit_log") == {
        "select": False,
        "insert": True,
        "update": False,
        "delete": False,
        "truncate": False,
    }
    async with engine.begin() as conn:
        await conn.execute(text(AUDIT_INSERT))
    for statement in (
        "SELECT count(*) FROM audit_log",
        "UPDATE audit_log SET actor = 'x'",
        "DELETE FROM audit_log",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO crawl_jobs (job_id, job_type, idempotency_key)"
                " VALUES (gen_random_uuid(), 'parse', 'parser-' || gen_random_uuid()::text)"
            )
        )
        await conn.execute(text("UPDATE crawl_jobs SET priority = 50 WHERE job_type = 'parse'"))
    # І при цьому parser не може видаляти jobs (retention — не його справа).
    assert (await _privileges(engine, "crawl_jobs"))["delete"] is False


@pytest.mark.parametrize("role", RUNTIME_ROLES)
async def test_no_runtime_role_can_update_or_delete_audit_log(
    role_engine: RoleEngine, role: str
) -> None:
    engine = await role_engine(role)
    privileges = await _privileges(engine, "audit_log")
    assert privileges["update"] is False, role
    assert privileges["delete"] is False, role
    for statement in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))


async def test_audit_log_append_only_trigger_applies_to_owner_too(
    role_engine: RoleEngine,
) -> None:
    """Навіть `collector_migrate` (owner таблиці) не може UPDATE/DELETE — це тригер, не GRANT."""
    engine = await role_engine("collector_migrate")
    async with engine.begin() as conn:
        owner = await conn.scalar(
            text("SELECT tableowner FROM pg_tables WHERE tablename = 'audit_log'")
        )
        assert owner == "collector_migrate"
        await conn.execute(text(AUDIT_INSERT))
    for statement in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM audit_log")) == 1


def test_migrate_role_is_not_referenced_by_runtime_code() -> None:
    """`collector_migrate` згадується лише у ролях/SQL ролей — жоден runtime-репозиторій не
    робить під ним `SET ROLE`/connect."""
    allowed = {
        Path("src/collector/persistence/postgres/roles.py"),
        Path("src/collector/persistence/postgres/sql/roles.sql"),
    }
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src").rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".sql"}
        and path.relative_to(REPO_ROOT) not in allowed
        and "collector_migrate" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"collector_migrate у runtime-коді: {offenders}"


def test_role_names_cover_every_component_of_section_13() -> None:
    assert set(ROLE_NAMES) == {
        "collector_migrate",
        "collector_scheduler",
        "collector_fetcher",
        "collector_parser",
        "collector_projector",
        "collector_translation",
        "collector_api_ro",
        "collector_export_ro",
    }
    assert "collector_migrate" not in RUNTIME_ROLES
