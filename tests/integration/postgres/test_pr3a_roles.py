"""PR3a п.10: кожна нова операція проходить під своєю LOGIN-роллю і відхиляється під чужою.

| Операція                                                            | Роль                  |
|---------------------------------------------------------------------|-----------------------|
| preflight, validators, retry budget, лічильник збоїв route          | `collector_fetcher`   |
| `fetch_unpublished` з лічильником видач, `unpark`, `purge_published` | `collector_scheduler` |
| fencing ack, retry/release projection з `not_before`, SR-4 запити   | `collector_projector` |
| enqueue `projection.reconcile`/`projection.compact`                 | `collector_scheduler` |
| `queue.release(not_before=)` (defer)                                | worker-ролі           |
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from collector.contracts.enums import DataDomain, FetchOutcome, RouteState, SourceState
from collector.persistence.postgres.engine import create_session_factory
from collector.persistence.postgres.repositories import (
    artifacts,
    outbox,
    projection,
    queue,
    reconciliation,
    sources,
)

from .conftest import FIXED_NOW, RoleEngine, make_entity, receipt, record

pytestmark = pytest.mark.integration

T0 = FIXED_NOW
URL = "https://news.example.test/a"


def _session(engine: AsyncEngine) -> AsyncSession:
    return create_session_factory(engine)()


async def _denied(engine: AsyncEngine, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with engine.begin() as conn:
            await conn.execute(text(statement))


async def _seed_source(session: AsyncSession) -> tuple[UUID, UUID]:
    async with session.begin():
        source = await sources.create_source(
            session,
            source_id="news_ua_roles",
            domain=DataDomain.NEWS,
            country="UA",
            state=SourceState.ENABLED,
            now=T0,
        )
        await sources.add_policy_version(
            session,
            source.id,
            sources.PolicySnapshot(
                requests_per_second=Decimal("0.2"),
                max_concurrency=1,
                crawl_interval_seconds=3600,
                robots_policy="respect",
                manifest_sha256="d" * 64,
            ),
            expected_revision=source.revision,
            actor="test",
            reason="seed",
            now=T0,
        )
        route = await sources.upsert_route(
            session, source.id, "rss", "https://news.example.test/rss", actor="test", reason="seed"
        )
    return source.id, route.id


async def test_fetcher_runs_preflight_validators_budget_and_route_counter(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    source_id, route_id = await _seed_source(pg_session)
    fetcher = _session(await role_engine("collector_fetcher"))
    async with fetcher:
        async with fetcher.begin():
            preflight = await sources.get_fetch_preflight(fetcher, source_id, route_id)
            assert preflight is not None and preflight.policy is not None
            await artifacts.record_fetch(
                fetcher,
                artifacts.FetchRecord(
                    requested_url=URL,
                    outcome=FetchOutcome.SUCCESS,
                    fetched_at=T0,
                    source_id=source_id,
                    http_status=200,
                    etag='"e"',
                ),
            )
            await artifacts.record_fetch(
                fetcher,
                artifacts.FetchRecord(
                    requested_url=URL,
                    outcome=FetchOutcome.RETRYABLE,
                    fetched_at=T0,
                    source_id=source_id,
                    http_status=503,
                ),
            )
        async with fetcher.begin():
            validators = await artifacts.latest_validators(fetcher, source_id, URL)
            assert validators is not None and validators.etag == '"e"'
            since = T0 - timedelta(days=1)
            assert await artifacts.count_retries_since(fetcher, source_id, since) == 1
            # Поріг 1: перехід у circuit_open + audit (INSERT audit_log у fetcher-а є).
            state = await sources.record_route_failure(
                fetcher, route_id, actor="fetch-1", reason="http_503", threshold=1, now=T0
            )
            assert state is RouteState.CIRCUIT_OPEN
            assert await sources.reset_route_failures(fetcher, route_id, now=T0) is state


async def test_fetcher_route_update_is_limited_to_counter_and_state_columns(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    _, route_id = await _seed_source(pg_session)
    fetcher = await role_engine("collector_fetcher")
    for assignment in ("route_key = 'https://evil.test/'", "route_kind = 'api'"):
        statement = f"UPDATE source_routes SET {assignment} WHERE id = '{route_id}'"  # noqa: S608 — фіксований перелік
        await _denied(fetcher, statement)
    await _denied(fetcher, "DELETE FROM outbox_events")


async def test_other_roles_cannot_touch_route_counter(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    _, route_id = await _seed_source(pg_session)
    for role in ("collector_parser", "collector_projector", "collector_api_ro"):
        session = _session(await role_engine(role))
        async with session:
            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin():
                    await sources.record_route_failure(
                        session, route_id, actor="x", reason="x", threshold=1, now=T0
                    )


async def test_scheduler_publishes_with_delivery_counter_unparks_and_purges(
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
    scheduler = _session(await role_engine("collector_scheduler"))
    async with scheduler:
        async with scheduler.begin():
            [event] = await outbox.fetch_unpublished(scheduler, max_delivery_attempts=1, now=T0)
        async with scheduler.begin():
            later = T0 + timedelta(minutes=5)
            assert (
                await outbox.fetch_unpublished(scheduler, max_delivery_attempts=1, now=later) == []
            )
            [parked] = await outbox.list_parked(scheduler)
            assert parked.last_error_code == outbox.DELIVERY_ATTEMPTS_EXHAUSTED
            await outbox.unpark(scheduler, event.outbox_id, actor="op", reason="fixed", now=later)
            [again] = await outbox.fetch_unpublished(scheduler, now=later)
            assert await outbox.mark_published(scheduler, [again.outbox_id], now=later) == 1
        async with scheduler.begin():
            purged = await outbox.purge_published(
                scheduler, older_than=timedelta(days=1), now=T0 + timedelta(days=2)
            )
            assert purged.deleted == 2  # internal (acknowledged) + domain (published)
            await queue.enqueue(
                scheduler,
                queue.NewJob(job_type="projection.reconcile", idempotency_key="reconcile:w1"),
                now=T0,
            )
            await queue.enqueue(
                scheduler,
                queue.NewJob(job_type="projection.compact", idempotency_key="compact:w1"),
                now=T0,
            )


async def test_purge_is_denied_to_parser_and_projector(role_engine: RoleEngine) -> None:
    for role in ("collector_parser", "collector_projector", "collector_api_ro"):
        await _denied(await role_engine(role), "DELETE FROM outbox_events")


async def test_projector_fencing_ack_defer_and_reconciler_queries(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    entity = await make_entity(pg_session)
    first = (await record(pg_session, entity.entity_uuid, 1)).task
    second = (await record(pg_session, entity.entity_uuid, 2)).task
    projector = _session(await role_engine("collector_projector"))
    async with projector:
        async with projector.begin():
            claimed = await projection.claim_projection_tasks(projector, "p-1", 60, limit=2, now=T0)
            assert {task.task_id for task in claimed} == {first.task_id, second.task_id}
            await projection.acknowledge_projection(
                projector,
                first.task_id,
                receipt(first.task_id, entity.entity_uuid, 1, applied=True, changed=False),
                owner="p-1",
                now=T0,
            )
            await projection.release_projection_task(
                projector, second.task_id, "p-1", not_before=T0 + timedelta(minutes=10), now=T0
            )
        async with projector.begin():
            [again] = await projection.claim_projection_tasks(
                projector, "p-1", 60, now=T0 + timedelta(minutes=10)
            )
            await projection.retry_projection_task(
                projector,
                again.task_id,
                "p-1",
                error_code="mongo_timeout",
                not_before=T0 + timedelta(hours=1),
                now=T0,
            )
        async with projector.begin():
            stale = await reconciliation.list_stale_projection_tasks(
                projector, older_than=timedelta(minutes=5), now=T0 + timedelta(hours=2)
            )
            assert [task.task_id for task in stale] == [second.task_id]
            state = await reconciliation.projection_completeness(
                projector, source_id="catalog_ua_example"
            )
            assert (state.open_tasks, state.quarantined_tasks) == (1, 0)
            assert await reconciliation.list_quarantined_projection_tasks(projector) == []


async def test_projector_cannot_update_outbox_publication_columns(
    role_engine: RoleEngine,
) -> None:
    projector = await role_engine("collector_projector")
    for column in ("published_at = now()", "parked_at = now()", "delivery_attempts = 0"):
        await _denied(projector, f"UPDATE outbox_events SET {column}")  # noqa: S608 — фіксований перелік


async def test_worker_role_defers_its_job_with_not_before(
    pg_session: AsyncSession, role_engine: RoleEngine
) -> None:
    async with pg_session.begin():
        await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="defer:1"), now=T0
        )
    fetcher = _session(await role_engine("collector_fetcher"))
    async with fetcher, fetcher.begin():
        [job] = await queue.claim(fetcher, ["fetch"], "f-1", 60, now=T0)
        deferred = await queue.release(
            fetcher, job.job_id, "f-1", not_before=T0 + timedelta(minutes=10), now=T0
        )
        assert (deferred.status, deferred.attempt) == ("pending", 0)
