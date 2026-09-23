"""LOGIN-ролі per component (§13; dependency WP-01D→WP-01A §2, знахідка F1).

Кожна runtime-роль підключається **власним** DSN (`postgres_dsn_<component>`) і може рівно те,
що їй потрібно:

- логін можливий лише після `--with-login`; `collector_migrate` LOGIN не отримує;
- жоден runtime-логін не superuser і не член `collector_migrate` (`verify_runtime_login`);
- репозиторні операції кожного компонента проходять під його роллю (fetcher: fetch + upload
  claim; parser: `record_parse_result`; projector: claim + ack; scheduler: outbox publisher);
- чужі дії відхиляються: fetcher не читає projection/outbox/entity index і `news_*`, parser
  не пише ack/change_events і не публікує outbox, projector не пише artifact pointers,
  `collector_api_ro` нічого не пише.

Ролі кластерні (спільні для всіх тестових БД), тому фікстура після тесту повертає їх у
NOLOGIN без пароля — інші тести бачать початковий стан.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from collector.contracts.enums import FetchOutcome
from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.engine import create_session_factory
from collector.persistence.postgres.ops import apply_database_roles
from collector.persistence.postgres.repositories import artifacts, outbox, projection
from collector.persistence.postgres.roles import (
    MIGRATE_ROLE,
    RUNTIME_ROLES,
    RoleLoginError,
    dsn_secret_name,
    load_role_logins,
    verify_runtime_login,
)

from .conftest import FIXED_NOW, make_entity, receipt, record

pytestmark = pytest.mark.integration

T0 = FIXED_NOW


@dataclass(frozen=True, slots=True)
class Logins:
    database: PostgresSettings
    secrets_dir: Path
    urls: dict[str, URL]


RoleEngine = Callable[[str], Awaitable[AsyncEngine]]


async def _reset_logins(admin: AsyncEngine) -> None:
    async with admin.connect() as conn:
        for role in RUNTIME_ROLES:
            await conn.execute(text(f'ALTER ROLE "{role}" WITH NOLOGIN PASSWORD NULL'))


def _write_secrets(database: PostgresSettings, directory: Path) -> dict[str, URL]:
    urls: dict[str, URL] = {}
    for role in RUNTIME_ROLES:
        url = database.url.set(username=role, password=secrets.token_hex(24))
        (directory / dsn_secret_name(role)).write_text(
            url.render_as_string(hide_password=False) + "\n", encoding="utf-8"
        )
        urls[role] = url
    return urls


@pytest.fixture
async def logins(pg_database: PostgresSettings, tmp_path: Path) -> AsyncIterator[Logins]:
    admin = create_async_engine(pg_database.url, isolation_level="AUTOCOMMIT", poolclass=None)
    urls = _write_secrets(pg_database, tmp_path)
    try:
        await apply_database_roles(pg_database, logins=load_role_logins(tmp_path))
        yield Logins(database=pg_database, secrets_dir=tmp_path, urls=urls)
    finally:
        await _reset_logins(admin)
        await admin.dispose()


@pytest.fixture
async def role_engine(logins: Logins) -> AsyncIterator[RoleEngine]:
    engines: list[AsyncEngine] = []

    async def make(role: str) -> AsyncEngine:
        engine = create_async_engine(logins.urls[role], poolclass=None)
        engines.append(engine)
        return engine

    try:
        yield make
    finally:
        for engine in engines:
            await engine.dispose()


async def _denied(engine: AsyncEngine, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with engine.begin() as conn:
            await conn.execute(text(statement))


async def test_runtime_roles_cannot_log_in_before_with_login(
    pg_database: PostgresSettings, tmp_path: Path
) -> None:
    urls = _write_secrets(pg_database, tmp_path)
    engine = create_async_engine(urls["collector_fetcher"], poolclass=None)
    try:
        # `trust` (CI) → «not permitted to log in»; `scram` → пароля в ролі ще немає.
        with pytest.raises(
            (DBAPIError, asyncpg.exceptions.InvalidAuthorizationSpecificationError),
            match="not permitted to log in|password authentication failed",
        ):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


async def test_every_runtime_role_logs_in_with_its_own_dsn_and_no_migrate_rights(
    logins: Logins, role_engine: RoleEngine
) -> None:
    for role in RUNTIME_ROLES:
        engine = await role_engine(role)
        async with engine.connect() as conn:
            assert await verify_runtime_login(conn) == role
            attributes = (
                await conn.execute(
                    text(
                        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
        assert tuple(attributes) == (True, False, False, False, False), role
    admin = create_async_engine(logins.database.url, poolclass=None)
    try:
        async with admin.connect() as conn:
            migrate_can_login = await conn.scalar(
                text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r"), {"r": MIGRATE_ROLE}
            )
            # Superuser/міграційний логін runtime використовувати не може.
            with pytest.raises(RoleLoginError, match="superuser"):
                await verify_runtime_login(conn)
    finally:
        await admin.dispose()
    assert migrate_can_login is False


async def test_with_login_is_idempotent_and_plain_roles_run_keeps_logins(
    logins: Logins, role_engine: RoleEngine
) -> None:
    await apply_database_roles(logins.database, logins=load_role_logins(logins.secrets_dir))
    await apply_database_roles(logins.database)  # `db roles` без --with-login
    engine = await role_engine("collector_parser")
    async with engine.connect() as conn:
        assert await verify_runtime_login(conn) == "collector_parser"


async def test_runtime_role_that_is_member_of_migrate_is_refused(logins: Logins) -> None:
    admin = create_async_engine(logins.database.url, isolation_level="AUTOCOMMIT", poolclass=None)
    try:
        async with admin.connect() as conn:
            await conn.execute(text("GRANT collector_migrate TO collector_fetcher"))
        try:
            with pytest.raises(RoleLoginError, match="collector_migrate"):
                await apply_database_roles(
                    logins.database, logins=load_role_logins(logins.secrets_dir)
                )
        finally:
            async with admin.connect() as conn:
                await conn.execute(text("REVOKE collector_migrate FROM collector_fetcher"))
    finally:
        await admin.dispose()


async def _session(engine: AsyncEngine) -> AsyncSession:
    return create_session_factory(engine)()


async def test_each_component_runs_its_repository_operations_under_its_own_role(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    """Grants достатні для реальних операцій компонента, а не лише для `SELECT 1`."""
    entity = await make_entity(pg_session)  # identity resolution — parser/discovery крок

    fetcher = await _session(await role_engine("collector_fetcher"))
    async with fetcher, fetcher.begin():
        claim = await artifacts.acquire_upload_claim(fetcher, "raw/x", "fetch-1", lease_seconds=30)
        await artifacts.commit_reference(
            fetcher,
            "raw/x",
            claim.claim_generation,
            owner="fetch-1",
            sha256="ab" * 32,
            size_bytes=1,
            uri="s3://raw/raw/x",
        )
        await artifacts.record_raw_object(
            fetcher,
            sha256="ab" * 32,
            object_key="raw/x",
            uri="s3://raw/raw/x",
            size_bytes=1,
            media_type="text/html",
        )
        await artifacts.record_fetch(
            fetcher,
            artifacts.FetchRecord(
                requested_url="https://x.test/", outcome=FetchOutcome.SUCCESS, fetched_at=T0
            ),
        )

    parser = await _session(await role_engine("collector_parser"))
    async with parser:
        task = (await record(parser, entity.entity_uuid, 1)).task

    projector = await _session(await role_engine("collector_projector"))
    async with projector:
        async with projector.begin():
            [claimed] = await projection.claim_projection_tasks(projector, "proj-1", 30, now=T0)
            assert claimed.task_id == task.task_id
        async with projector.begin():
            result = await projection.acknowledge_projection(
                projector,
                task.task_id,
                receipt(task.task_id, entity.entity_uuid, 1, applied=True, changed=True),
                now=T0,
            )
    assert result.created and result.outbox_event is not None

    scheduler = await _session(await role_engine("collector_scheduler"))
    async with scheduler, scheduler.begin():
        batch = await outbox.fetch_unpublished(scheduler, now=T0)
        assert len(batch) == 2
        assert await outbox.mark_published(scheduler, [e.outbox_id for e in batch], now=T0) == 2
        await projection.recover_expired_projection_leases(scheduler, now=T0)
        await artifacts.expire_claims(scheduler, now=T0)


async def test_components_cannot_do_each_others_work(role_engine: RoleEngine) -> None:
    fetcher = await role_engine("collector_fetcher")
    for table in (
        "projection_tasks",
        "projection_acknowledgements",
        "entity_index",
        "normalized_artifacts",
        "change_events",
        "outbox_events",
        "audit_log",
    ):
        await _denied(fetcher, f"SELECT count(*) FROM {table}")  # noqa: S608 — фіксований перелік
    async with fetcher.connect() as conn:
        # `news_*` з'являться в PR3; перевірка вже тепер пройде по всіх таблицях з префіксом.
        readable_news = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename LIKE 'news\\_%' "
                    "AND has_table_privilege(current_user, tablename, 'SELECT')"
                )
            )
        ).all()
    assert readable_news == []

    parser = await role_engine("collector_parser")
    await _denied(
        parser,
        "INSERT INTO projection_acknowledgements (task_id, entity_uuid, projection_version, "
        "receipt_id, receipt_cluster_time, applied_to_current, state_changed, result_version, "
        "result_hash, acknowledged_at) VALUES (gen_random_uuid(), gen_random_uuid(), 1, "
        "gen_random_uuid(), '1:1', true, true, 1, 'v1:' || repeat('0', 64), now())",
    )
    await _denied(parser, "UPDATE outbox_events SET published_at = now()")
    await _denied(parser, "DELETE FROM projection_tasks")
    await _denied(parser, "UPDATE projection_tasks SET status = 'succeeded'")

    projector = await role_engine("collector_projector")
    await _denied(
        projector,
        "INSERT INTO parse_attempts (parse_attempt_id, raw_sha256, domain, parser_version, "
        "outcome) VALUES (gen_random_uuid(), repeat('0', 64), 'catalog', 'p', 'succeeded')",
    )
    await _denied(projector, "UPDATE normalized_artifacts SET uri = 'x'")
    await _denied(projector, "UPDATE outbox_events SET published_at = now()")

    for role in ("collector_api_ro", "collector_export_ro"):
        read_only = await role_engine(role)
        async with read_only.connect() as conn:
            await conn.execute(text("SELECT count(*) FROM outbox_events"))
        await _denied(read_only, "UPDATE entity_index SET confirmed_projection_version = 0")
        await _denied(read_only, "DELETE FROM fetches")


async def _confirmed_entity(session: AsyncSession) -> str:
    """Сутність з `projection_version = 2`, `confirmed_projection_version = 2` (під admin)."""
    entity = await make_entity(session)
    for n in (1, 2):
        task = (await record(session, entity.entity_uuid, n)).task
        async with session.begin():
            await projection.acknowledge_projection(
                session,
                task.task_id,
                receipt(task.task_id, entity.entity_uuid, n, applied=True, changed=True),
                now=T0,
            )
    return str(entity.entity_uuid)


async def _rejected(engine: AsyncEngine, statement: str, pattern: str, uuid: str) -> None:
    with pytest.raises(DBAPIError, match=pattern):
        async with engine.begin() as conn:
            await conn.execute(text(statement), {"u": uuid})


async def test_parser_and_projector_cannot_lower_entity_versions(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    """Gate 2, F-1: column-level GRANT + тригер `entity_index_versions_monotonic`."""
    uuid = await _confirmed_entity(pg_session)
    parser = await role_engine("collector_parser")
    # Колонки, яких parser не пише, — поза його GRANT.
    await _rejected(
        parser,
        "UPDATE entity_index SET confirmed_projection_version = 0 WHERE entity_uuid = :u",
        "permission denied",
        uuid,
    )
    await _rejected(
        parser,
        "UPDATE entity_index SET canonical_url = 'x' WHERE entity_uuid = :u",
        "permission denied",
        uuid,
    )
    # Власну колонку зменшити не можна: тригер.
    await _rejected(
        parser,
        "UPDATE entity_index SET projection_version = 0 WHERE entity_uuid = :u",
        "не зменшуються",
        uuid,
    )

    projector = await role_engine("collector_projector")
    await _rejected(
        projector,
        "UPDATE entity_index SET projection_version = 0 WHERE entity_uuid = :u",
        "permission denied",
        uuid,
    )
    await _rejected(
        projector,
        "UPDATE entity_index SET confirmed_projection_version = 1 WHERE entity_uuid = :u",
        "не зменшуються",
        uuid,
    )
    async with pg_session.begin():
        row = (
            await pg_session.execute(
                text(
                    "SELECT projection_version, confirmed_projection_version FROM entity_index "
                    "WHERE entity_uuid = :u"
                ),
                {"u": uuid},
            )
        ).one()
    assert tuple(row) == (2, 2)


async def test_version_guard_applies_even_to_the_superuser(pg_session: AsyncSession) -> None:
    uuid = await _confirmed_entity(pg_session)
    with pytest.raises(DBAPIError, match="не зменшуються"):
        async with pg_session.begin():
            await pg_session.execute(
                text(
                    "UPDATE entity_index SET confirmed_projection_version = 1 "
                    "WHERE entity_uuid = :u"
                ),
                {"u": uuid},
            )


async def test_parser_cannot_forge_domain_outbox_events(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    """Gate 2, F-2: RLS — parser вставляє й бачить лише `topic='internal'`."""
    await _confirmed_entity(pg_session)  # 2 internal + 2 domain рядки
    insert = (
        "INSERT INTO outbox_events (outbox_id, event_id, topic, event_type, aggregate_id, "
        "aggregate_version, payload_schema_version, payload_bytes, payload_media_type, "
        "payload_sha256) VALUES (gen_random_uuid(), gen_random_uuid(), :topic, "
        "'catalog.item.changed', gen_random_uuid(), 1, '1.0', '\x7b7d'::bytea, "
        "'application/json', repeat('0', 64))"
    )
    parser = await role_engine("collector_parser")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with parser.begin() as conn:
            await conn.execute(text(insert), {"topic": "domain"})
    async with parser.begin() as conn:
        await conn.execute(text(insert), {"topic": "internal"})
        topics = set((await conn.execute(text("SELECT topic FROM outbox_events"))).scalars())
    assert topics == {"internal"}
    # Publisher (scheduler) і projector бачать обидва топіки.
    for role in ("collector_scheduler", "collector_projector"):
        engine = await role_engine(role)
        async with engine.connect() as conn:
            seen = set((await conn.execute(text("SELECT topic FROM outbox_events"))).scalars())
        assert seen == {"internal", "domain"}, role
