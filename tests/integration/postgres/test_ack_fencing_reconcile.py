"""PR3a п.7 (fencing ack) і п.9 (PG-запити reconciler-а §7.3 крок 5, SR-4).

Fencing: runtime projector-а (WP-01D PR1c п.5) робить ack у report-транзакції з
`owner=worker_instance_id`; worker, у якого lease забрали, не підтверджує чужу спробу.
Reconciler підтверджує без `owner` (рішення WP-01B п.1).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.persistence.postgres.errors import LeaseNotOwnedError
from collector.persistence.postgres.models import (
    ChangeEvent,
    EntityIndex,
    OutboxEvent,
    ProjectionAcknowledgement,
    ProjectionTask,
)
from collector.persistence.postgres.repositories import projection, reconciliation

from .conftest import FIXED_NOW, make_entity, receipt, record

pytestmark = pytest.mark.integration

T0 = FIXED_NOW


async def test_projection_completeness_rejects_naive_watermark_before_sql(
    pg_session: AsyncSession,
) -> None:
    naive = datetime(2026, 9, 24, 12)  # noqa: DTZ001 — contract under test
    with pytest.raises(ValueError, match="created_before.*aware"):
        await reconciliation.projection_completeness(pg_session, created_before=naive)


async def _counts(session: AsyncSession) -> tuple[int, int, int]:
    async with session.begin():
        acks = await session.scalar(select(func.count()).select_from(ProjectionAcknowledgement))
        changes = await session.scalar(select(func.count()).select_from(ChangeEvent))
        domain = await session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "domain")
        )
    return int(acks or 0), int(changes or 0), int(domain or 0)


async def _claimed(session: AsyncSession, n: int = 1, item: str = "item-1") -> ProjectionTask:
    entity = await make_entity(session, item)
    await record(session, entity.entity_uuid, n)
    async with session.begin():
        [task] = await projection.claim_projection_tasks(session, "p-1", 60, now=T0)
    return task


async def test_ack_with_foreign_owner_is_rejected_without_any_write(
    pg_session: AsyncSession,
) -> None:
    task = await _claimed(pg_session)
    task_id, entity_uuid = task.task_id, task.entity_uuid  # rollback експайрить ORM-об'єкти
    with pytest.raises(LeaseNotOwnedError):
        async with pg_session.begin():
            await projection.acknowledge_projection(
                pg_session,
                task_id,
                receipt(task_id, entity_uuid, 1, applied=True, changed=True),
                owner="p-other",
                now=T0,
            )
    assert await _counts(pg_session) == (0, 0, 0)
    async with pg_session.begin():
        current = await pg_session.get(ProjectionTask, task_id, populate_existing=True)
        entity = await pg_session.get(EntityIndex, entity_uuid, populate_existing=True)
    assert current is not None and (current.status, current.lease_owner) == ("leased", "p-1")
    assert entity is not None and entity.confirmed_projection_version == 0


async def test_ack_by_lease_owner_succeeds(pg_session: AsyncSession) -> None:
    task = await _claimed(pg_session)
    async with pg_session.begin():
        result = await projection.acknowledge_projection(
            pg_session,
            task.task_id,
            receipt(task.task_id, task.entity_uuid, 1, applied=True, changed=True),
            owner="p-1",
            now=T0,
        )
    assert result.created and result.confirmed_projection_version == 1
    assert await _counts(pg_session) == (1, 1, 1)


async def test_ack_after_lease_was_taken_over_is_rejected_and_new_owner_acks_once(
    pg_session: AsyncSession,
) -> None:
    task = await _claimed(pg_session)
    task_id, entity_uuid = task.task_id, task.entity_uuid
    later = T0 + timedelta(minutes=5)
    async with pg_session.begin():
        assert await projection.recover_expired_projection_leases(pg_session, now=later) == [
            task_id
        ]
        [again] = await projection.claim_projection_tasks(pg_session, "p-2", 60, now=later)
    assert again.task_id == task_id
    applied = receipt(task_id, entity_uuid, 1, applied=True, changed=True)
    with pytest.raises(LeaseNotOwnedError):
        async with pg_session.begin():
            await projection.acknowledge_projection(
                pg_session, task_id, applied, owner="p-1", now=later
            )
    async with pg_session.begin():
        await projection.acknowledge_projection(
            pg_session, task_id, applied, owner="p-2", now=later
        )
    assert await _counts(pg_session) == (1, 1, 1)
    # Повтор із owner після успіху — lease уже немає.
    with pytest.raises(LeaseNotOwnedError):
        async with pg_session.begin():
            await projection.acknowledge_projection(
                pg_session, task_id, applied, owner="p-2", now=later
            )


async def test_ack_without_owner_keeps_reconciler_behaviour(pg_session: AsyncSession) -> None:
    """Reconciler: task не leased (lease відновлено), ack зі збереженого receipt без owner."""
    task = await _claimed(pg_session)
    async with pg_session.begin():
        await projection.recover_expired_projection_leases(
            pg_session, now=T0 + timedelta(minutes=5)
        )
    applied = receipt(task.task_id, task.entity_uuid, 1, applied=True, changed=True)
    async with pg_session.begin():
        first = await projection.acknowledge_projection(pg_session, task.task_id, applied, now=T0)
    async with pg_session.begin():
        again = await projection.acknowledge_projection(pg_session, task.task_id, applied, now=T0)
    assert (first.created, again.created) == (True, False)
    assert await _counts(pg_session) == (1, 1, 1)


# --- SR-4: reconciler queries ------------------------------------------------------------


async def _ack(session: AsyncSession, task: ProjectionTask, version: int) -> None:
    async with session.begin():
        await projection.acknowledge_projection(
            session,
            task.task_id,
            receipt(task.task_id, task.entity_uuid, version, applied=True, changed=False),
            now=T0,
        )


async def test_stale_tasks_lists_long_leased_and_long_claimable_with_keyset(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    tasks: list[UUID] = []
    for n in range(1, 5):
        tasks.append((await record(pg_session, entity.entity_uuid, n)).task.task_id)
    async with pg_session.begin():
        [leased] = await projection.claim_projection_tasks(pg_session, "p-1", 3600, now=T0)
    fresh_cutoff = timedelta(minutes=10)
    async with pg_session.begin():
        assert (
            await reconciliation.list_stale_projection_tasks(
                pg_session, older_than=fresh_cutoff, now=T0 + timedelta(minutes=5)
            )
            == []
        )
        stale = await reconciliation.list_stale_projection_tasks(
            pg_session, older_than=fresh_cutoff, now=T0 + timedelta(minutes=30)
        )
    assert sorted(task.task_id for task in stale) == sorted(tasks)
    assert leased.task_id in {task.task_id for task in stale}
    # Keyset: сторінки по 2 без OFFSET, разом — усі, без повторів.
    pages: list[UUID] = []
    after: UUID | None = None
    async with pg_session.begin():
        while True:
            page = await reconciliation.list_stale_projection_tasks(
                pg_session,
                older_than=fresh_cutoff,
                after=after,
                limit=2,
                now=T0 + timedelta(minutes=30),
            )
            if not page:
                break
            pages.extend(task.task_id for task in page)
            after = page[-1].task_id
    assert pages == sorted(tasks)


async def test_stale_tasks_rejects_negative_age_before_sql(pg_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="older_than"):
        await reconciliation.list_stale_projection_tasks(
            pg_session, older_than=timedelta(microseconds=-1), now=T0
        )


async def test_quarantined_task_blocks_completeness_but_not_drift_counter(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    first = (await record(pg_session, entity.entity_uuid, 1)).task
    second = (await record(pg_session, entity.entity_uuid, 2)).task
    other = await make_entity(pg_session, "other-item")
    foreign = (await record(pg_session, other.entity_uuid, 3)).task

    async with pg_session.begin():
        before = await reconciliation.projection_completeness(
            pg_session, entity_uuid=entity.entity_uuid
        )
    assert (before.open_tasks, before.quarantined_tasks, before.complete) == (2, 0, False)
    assert before.oldest_open_task_created_at == T0

    await _ack(pg_session, first, 1)
    async with pg_session.begin():
        await projection.quarantine_projection_task(
            pg_session, second.task_id, error_code="bad_payload", now=T0
        )
        state = await reconciliation.projection_completeness(
            pg_session, entity_uuid=entity.entity_uuid
        )
        by_source = await reconciliation.projection_completeness(
            pg_session, source_id="catalog_ua_example"
        )
        quarantined = await reconciliation.list_quarantined_projection_tasks(
            pg_session, source_id="catalog_ua_example"
        )
    # Quarantined не рахується в drift (open=0, settled за §7.3), але блокує «повноту».
    assert (state.open_tasks, state.quarantined_tasks) == (0, 1)
    assert state.settled and not state.complete
    # Джерело: чужа сутність того самого source_id ще відкрита.
    assert (by_source.open_tasks, by_source.quarantined_tasks) == (1, 1)
    assert [task.task_id for task in quarantined] == [second.task_id]

    await _ack(pg_session, foreign, 1)
    async with pg_session.begin():
        watermark = await reconciliation.projection_completeness(
            pg_session, source_id="catalog_ua_example", created_before=T0
        )
        unknown = await reconciliation.projection_completeness(pg_session, source_id="nope")
    # Watermark до створення tasks — нічого не бачить; невідоме джерело — повне.
    assert watermark.complete and unknown.complete


async def test_completeness_reports_unpublished_domain_events_separately(
    pg_session: AsyncSession,
) -> None:
    task = await _claimed(pg_session)
    async with pg_session.begin():
        await projection.acknowledge_projection(
            pg_session,
            task.task_id,
            receipt(task.task_id, task.entity_uuid, 1, applied=True, changed=True),
            now=T0,
        )
        state = await reconciliation.projection_completeness(
            pg_session, entity_uuid=task.entity_uuid
        )
    assert state.complete
    assert state.unpublished_domain_events == 1
    assert state.oldest_unpublished_domain_event_created_at == T0
