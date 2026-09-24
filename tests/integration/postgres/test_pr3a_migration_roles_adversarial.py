"""PR3a — міграція `0006` поверх наявних даних і права нових операцій під чужими LOGIN-ролями
(незалежний тестувальник, testing-pr3a.md).

- `0005` з даними → `upgrade 0006`: `delivery_attempts = 0` для наявних рядків,
  `requested_url_md5` обчислено для наявних fetches у всіх партиціях, partial index є на кожній
  партиції; `downgrade 0005` зберігає дані; повторний `upgrade` → без drift;
- кожна нова операція PR3a під роллю, якій вона не призначена, → permission denied.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from collector.contracts import new_entity_id
from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.engine import create_session_factory
from collector.persistence.postgres.migrations import (
    _run_with_connection,
    check_no_drift,
    current_revision,
    head_revision,
    upgrade_to_head,
)
from collector.persistence.postgres.repositories import (
    artifacts,
    outbox,
    projection,
    queue,
    reconciliation,
    sources,
)

from .conftest import FIXED_NOW, RoleEngine, make_entity, receipt, record
from .test_pr3a_roles import _seed_source

pytestmark = pytest.mark.integration

T0 = FIXED_NOW
PREV = "0005_entity_version_guard"
URL = "https://news.example.test/a"


async def _migrate(engine: AsyncEngine, action: str, revision: str) -> None:
    def run(cfg: object) -> None:
        getattr(command, action)(cfg, revision)

    async with engine.begin() as conn:
        await conn.run_sync(_run_with_connection, run, None)


async def test_upgrade_0006_over_existing_rows_then_downgrade_and_upgrade_again(
    pg_empty_database: PostgresSettings,
) -> None:
    engine = create_async_engine(pg_empty_database.url, poolclass=None)
    outbox_id, fetch_id = new_entity_id(), new_entity_id()
    try:
        await _migrate(engine, "upgrade", PREV)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO outbox_events (outbox_id, event_id, topic, event_type,"
                    " aggregate_id, aggregate_version, payload_schema_version, payload_bytes,"
                    " payload_media_type, payload_sha256, attempts)"
                    " VALUES (:o, :e, 'domain', 'catalog.item.changed', :a, 1, '1.0', :b,"
                    " 'application/json', :h, 2)"
                ),
                {
                    "o": outbox_id,
                    "e": new_entity_id(),
                    "a": new_entity_id(),
                    "b": b"{}",
                    "h": "a" * 64,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO fetches (fetch_id, fetched_at, source_id, requested_url,"
                    " outcome, http_status, etag) VALUES (:f, :t, :s, :u, 'success', 200, 'e1')"
                ),
                {"f": fetch_id, "t": T0, "s": new_entity_id(), "u": URL},
            )
        await _migrate(engine, "upgrade", "0006_queue_outbox_preflight")
        async with engine.connect() as conn:
            assert await current_revision(conn) == "0006_queue_outbox_preflight"
            row = (
                await conn.execute(
                    text(
                        "SELECT delivery_attempts, attempts FROM outbox_events WHERE outbox_id=:o"
                    ),
                    {"o": outbox_id},
                )
            ).one()
            assert tuple(row) == (0, 2)
            md5 = await conn.scalar(
                text(
                    "SELECT requested_url_md5 = md5(requested_url) FROM fetches WHERE fetch_id=:f"
                ),
                {"f": fetch_id},
            )
            assert md5 is True
            # Partial index поширено на кожну наявну партицію fetches.
            missing = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
                    " WHERE i.inhparent = 'fetches'::regclass AND NOT EXISTS ("
                    "  SELECT 1 FROM pg_index x JOIN pg_class ic ON ic.oid = x.indexrelid"
                    "  WHERE x.indrelid = c.oid AND pg_get_indexdef(x.indexrelid) LIKE"
                    "  '%requested_url_md5%')"
                )
            )
            assert missing == 0
            with pytest.raises(DBAPIError, match="check"):
                async with conn.begin_nested():
                    await conn.execute(text("UPDATE outbox_events SET delivery_attempts = -1"))
        await _migrate(engine, "downgrade", PREV)
        async with engine.connect() as conn:
            assert await current_revision(conn) == PREV
            cols = set(
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns"
                            " WHERE table_name IN ('outbox_events', 'fetches')"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert not {"delivery_attempts", "requested_url_md5"} & cols
            assert await conn.scalar(text("SELECT count(*) FROM outbox_events")) == 1
            assert await conn.scalar(text("SELECT count(*) FROM fetches")) == 1
        async with engine.begin() as conn:
            await upgrade_to_head(conn)
        async with engine.connect() as conn:
            assert await current_revision(conn) == head_revision()
            assert await check_no_drift(conn) == []
    finally:
        await engine.dispose()


# --- roles: new operations under foreign LOGIN roles ----------------------------------------


def _session(engine: AsyncEngine) -> AsyncSession:
    return create_session_factory(engine)()


async def _denied(engine: AsyncEngine, op: object) -> None:
    session = _session(engine)
    async with session:
        with pytest.raises(DBAPIError, match="permission denied"):
            async with session.begin():
                await op(session)  # type: ignore[operator]


async def test_fetch_reads_are_denied_to_roles_without_fetch_grants(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    source_id, route_id = await _seed_source(pg_session)
    since = T0 - timedelta(days=1)
    for role in ("collector_translation", "collector_projector"):
        engine = await role_engine(role)
        await _denied(engine, lambda s: sources.get_fetch_preflight(s, source_id, route_id))
        await _denied(engine, lambda s: artifacts.count_retries_since(s, source_id, since))
        await _denied(engine, lambda s: artifacts.latest_validators(s, source_id, URL))
    # translation не має жодних прав на source_routes.
    await _denied(
        await role_engine("collector_translation"),
        lambda s: sources.reset_route_failures(s, route_id, now=T0),
    )


async def test_outbox_publisher_ops_are_denied_outside_scheduler(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    entity = await make_entity(pg_session)
    task = (await record(pg_session, entity.entity_uuid, 1)).task
    async with pg_session.begin():
        await projection.acknowledge_projection(
            pg_session,
            task.task_id,
            receipt(task.task_id, entity.entity_uuid, 1, applied=True, changed=True),
            now=T0,
        )
    for role in (
        "collector_projector",
        "collector_parser",
        "collector_fetcher",
        "collector_api_ro",
    ):
        engine = await role_engine(role)
        await _denied(engine, lambda s: outbox.fetch_unpublished(s, now=T0))
        await _denied(
            engine,
            lambda s: outbox.purge_published(
                s, older_than=timedelta(0), now=T0 + timedelta(days=1)
            ),
        )


async def test_projection_fencing_and_defer_are_denied_to_non_projector_roles(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    entity = await make_entity(pg_session)
    await record(pg_session, entity.entity_uuid, 1)
    async with pg_session.begin():
        [task] = await projection.claim_projection_tasks(pg_session, "p-1", 60, now=T0)
    task_id, entity_uuid = task.task_id, task.entity_uuid
    applied = receipt(task_id, entity_uuid, 1, applied=True, changed=False)
    for role in ("collector_parser", "collector_fetcher", "collector_api_ro"):
        engine = await role_engine(role)
        await _denied(
            engine,
            lambda s: projection.acknowledge_projection(s, task_id, applied, owner="p-1", now=T0),
        )
        await _denied(
            engine,
            lambda s: projection.release_projection_task(
                s, task_id, "p-1", not_before=T0 + timedelta(minutes=5), now=T0
            ),
        )
    # Scheduler має column UPDATE projection_tasks для recover/quarantine, але не `not_before`/
    # `attempt` — defer projection task йому недоступний.
    await _denied(
        await role_engine("collector_scheduler"),
        lambda s: projection.release_projection_task(
            s, task_id, "p-1", not_before=T0 + timedelta(minutes=5), now=T0
        ),
    )


async def test_reconciler_queries_are_denied_to_fetcher_and_translation(
    role_engine: RoleEngine,
) -> None:
    for role in ("collector_fetcher", "collector_translation"):
        engine = await role_engine(role)
        await _denied(
            engine,
            lambda s: reconciliation.list_stale_projection_tasks(
                s, older_than=timedelta(minutes=5), now=T0
            ),
        )
        await _denied(engine, lambda s: reconciliation.projection_completeness(s))
        await _denied(engine, lambda s: reconciliation.list_quarantined_projection_tasks(s))


async def test_queue_defer_is_denied_to_read_only_roles(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    async with pg_session.begin():
        await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="ro:1"), now=T0
        )
        [job] = await queue.claim(pg_session, ["fetch"], "f-1", 60, now=T0)
    job_id: UUID = job.job_id
    for role in ("collector_api_ro", "collector_export_ro"):
        await _denied(
            await role_engine(role),
            lambda s: queue.release(s, job_id, "f-1", not_before=T0 + timedelta(minutes=5), now=T0),
        )
