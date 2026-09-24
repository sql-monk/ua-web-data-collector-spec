"""PR3a п.2–3: лічильник видач outbox (N-2, варіант 2) і `purge_published` для `internal`.

N-2: publisher «помирає» після `fetch_unpublished` (транзакція закомічена, `mark_failed` не
викликано) — раніше рядок повертався після visibility lease нескінченно. Тепер видача
рахується в тій самій lease-транзакції, і після `max_delivery_attempts` рядок паркується.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.persistence.postgres.models import OutboxEvent
from collector.persistence.postgres.repositories import outbox, projection, queue

from .conftest import FIXED_NOW, make_entity, receipt, record

pytestmark = pytest.mark.integration

T0 = FIXED_NOW
VISIBILITY = 60


async def _acked_domain_event(session: AsyncSession, n: int) -> tuple[UUID, UUID]:
    """Сутність `item-n` з однією acknowledged task (artifact `n` — унікальний object key) і
    одним `domain.changed`: (task_id, outbox_id події)."""
    entity = await make_entity(session, f"item-{n}")
    task = (await record(session, entity.entity_uuid, n)).task
    async with session.begin():
        result = await projection.acknowledge_projection(
            session,
            task.task_id,
            receipt(task.task_id, entity.entity_uuid, 1, applied=True, changed=True),
            now=T0,
        )
    assert result.outbox_event is not None
    return task.task_id, result.outbox_event.outbox_id


async def _crash_after_fetch(
    session: AsyncSession, now: datetime, *, limit: int = 10, max_delivery_attempts: int = 3
) -> list[OutboxEvent]:
    """Publisher видав рядки і впав: lease-транзакцію закомічено, `mark_failed` немає."""
    async with session.begin():
        return await outbox.fetch_unpublished(
            session,
            limit=limit,
            visibility_seconds=VISIBILITY,
            max_delivery_attempts=max_delivery_attempts,
            now=now,
        )


async def test_crashing_publisher_parks_poison_row_after_max_delivery_attempts(
    pg_session: AsyncSession,
) -> None:
    _, poison = await _acked_domain_event(pg_session, 10)
    now = T0
    for expected in (1, 2, 3):
        batch = await _crash_after_fetch(pg_session, now)
        assert [event.outbox_id for event in batch] == [poison]
        assert batch[0].delivery_attempts == expected
        now += timedelta(seconds=VISIBILITY)
    # Четверта вибірка: межу досягнуто → паркування замість видачі.
    assert await _crash_after_fetch(pg_session, now) == []
    async with pg_session.begin():
        row = await pg_session.get(OutboxEvent, poison, populate_existing=True)
        assert row is not None
        assert row.parked_at == now
        assert row.last_error_code == outbox.DELIVERY_ATTEMPTS_EXHAUSTED
        assert (row.delivery_attempts, row.attempts, row.published_at) == (3, 0, None)
        assert [event.outbox_id for event in await outbox.list_parked(pg_session)] == [poison]


async def test_parked_poison_row_does_not_block_the_rest(pg_session: AsyncSession) -> None:
    _, poison = await _acked_domain_event(pg_session, 11)
    now = T0
    for _ in range(2):
        await _crash_after_fetch(pg_session, now, max_delivery_attempts=2)
        now += timedelta(seconds=VISIBILITY)
    _, healthy = await _acked_domain_event(pg_session, 12)
    # Та сама вибірка: poison паркується, healthy видається — без head-of-line blocking.
    batch = await _crash_after_fetch(pg_session, now, max_delivery_attempts=2)
    assert [event.outbox_id for event in batch] == [healthy]
    async with pg_session.begin():
        parked = await outbox.list_parked(pg_session)
    assert [event.outbox_id for event in parked] == [poison]


async def test_mark_failed_and_redelivery_do_not_count_one_attempt_twice(
    pg_session: AsyncSession,
) -> None:
    _, event_id = await _acked_domain_event(pg_session, 13)
    [first] = await _crash_after_fetch(pg_session, T0)
    policy = queue.BackoffPolicy(base=timedelta(seconds=1), jitter_ratio=0.0)
    async with pg_session.begin():
        failed = await outbox.mark_failed(
            pg_session, first.outbox_id, error_code="sink_down", policy=policy, now=T0
        )
    assert (failed.attempts, failed.delivery_attempts) == (1, 1)
    [second] = await _crash_after_fetch(pg_session, T0 + timedelta(seconds=5))
    assert second.outbox_id == event_id
    assert (second.attempts, second.delivery_attempts) == (1, 2)


async def test_unpark_resets_delivery_counter(pg_session: AsyncSession) -> None:
    _, event_id = await _acked_domain_event(pg_session, 14)
    now = T0
    for _ in range(2):
        await _crash_after_fetch(pg_session, now, max_delivery_attempts=1)
        now += timedelta(seconds=VISIBILITY)
    async with pg_session.begin():
        restored = await outbox.unpark(
            pg_session, event_id, actor="operator", reason="sink fixed", now=now
        )
    assert (restored.parked_at, restored.delivery_attempts, restored.attempts) == (None, 0, 0)
    [again] = await _crash_after_fetch(pg_session, now, max_delivery_attempts=1)
    assert again.outbox_id == event_id


async def test_internal_topic_is_not_delivered_by_default(pg_session: AsyncSession) -> None:
    entity = await make_entity(pg_session)
    await record(pg_session, entity.entity_uuid, 1)
    assert await _crash_after_fetch(pg_session, T0) == []
    async with pg_session.begin():
        internal = (await pg_session.execute(select(OutboxEvent))).scalars().one()
    assert (internal.topic, internal.delivery_attempts) == ("internal", 0)


async def test_invalid_max_delivery_attempts_is_rejected(pg_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="max_delivery_attempts"):
        await outbox.fetch_unpublished(pg_session, max_delivery_attempts=0, now=T0)


# --- purge_published ---------------------------------------------------------------------


async def _topics(session: AsyncSession) -> list[tuple[str, UUID]]:
    async with session.begin():
        rows = (await session.execute(select(OutboxEvent.topic, OutboxEvent.event_id))).all()
    return sorted((topic, event_id) for topic, event_id in rows)


async def test_purge_deletes_acknowledged_internal_and_old_published_domain(
    pg_session: AsyncSession,
) -> None:
    task_id, domain_outbox = await _acked_domain_event(pg_session, 15)
    async with pg_session.begin():
        assert await outbox.mark_published(pg_session, [domain_outbox], now=T0) == 1
        result = await outbox.purge_published(
            pg_session, older_than=timedelta(days=1), now=T0 + timedelta(days=2)
        )
    assert result.deleted == 2
    assert await _topics(pg_session) == []
    async with pg_session.begin():
        # Наступна сторінка після останнього event_id — порожня, прохід завершено.
        tail = await outbox.purge_published(
            pg_session,
            older_than=timedelta(days=1),
            after=result.last_event_id,
            now=T0 + timedelta(days=2),
        )
    assert tail == outbox.PurgeResult(deleted=0, last_event_id=None)
    async with pg_session.begin():
        task = await projection.get_projection_task(pg_session, task_id)
    assert task is not None and task.status == "succeeded", "purge не чіпає tasks"


async def test_purge_keeps_unacknowledged_quarantined_unpublished_and_young_rows(
    pg_session: AsyncSession,
) -> None:
    pending_entity = await make_entity(pg_session, "pending")
    pending = (await record(pg_session, pending_entity.entity_uuid, 1)).task
    quarantined_entity = await make_entity(pg_session, "quarantined")
    quarantined = (await record(pg_session, quarantined_entity.entity_uuid, 2)).task
    async with pg_session.begin():
        await projection.quarantine_projection_task(
            pg_session, quarantined.task_id, error_code="bad_payload", now=T0
        )
    _, unpublished_domain = await _acked_domain_event(pg_session, 16)
    before = await _topics(pg_session)
    async with pg_session.begin():
        result = await outbox.purge_published(
            pg_session, older_than=timedelta(days=1), now=T0 + timedelta(days=2)
        )
    # Лише internal-команда acknowledged task «unpublished» — решта лишається.
    assert result.deleted == 1
    after = await _topics(pg_session)
    assert ("internal", pending.task_id) in after
    assert ("internal", quarantined.task_id) in after
    assert len(after) == len(before) - 1
    async with pg_session.begin():
        assert await pg_session.get(OutboxEvent, unpublished_domain) is not None
        # Опублікований, але молодший за поріг рядок теж лишається.
        await outbox.mark_published(pg_session, [unpublished_domain], now=T0 + timedelta(days=2))
        young = await outbox.purge_published(
            pg_session, older_than=timedelta(days=1), now=T0 + timedelta(days=2, hours=1)
        )
    assert young.deleted == 0


async def test_purge_is_batched_by_event_id_keyset(pg_session: AsyncSession) -> None:
    for n in range(3):
        _, domain_outbox = await _acked_domain_event(pg_session, 20 + n)
        async with pg_session.begin():
            await outbox.mark_published(pg_session, [domain_outbox], now=T0)
    later = T0 + timedelta(days=2)
    deleted = 0
    after: UUID | None = None
    batches = 0
    while True:
        async with pg_session.begin():
            result = await outbox.purge_published(
                pg_session, older_than=timedelta(days=1), after=after, limit=2, now=later
            )
        if result.last_event_id is None:
            break
        assert result.deleted <= 2
        deleted += result.deleted
        after = result.last_event_id
        batches += 1
    assert (deleted, batches) == (6, 3)
    assert await _topics(pg_session) == []


async def test_repeating_parse_step_after_purge_returns_existing_task(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    first = await record(pg_session, entity.entity_uuid, 1)
    async with pg_session.begin():
        await projection.acknowledge_projection(
            pg_session,
            first.task.task_id,
            receipt(first.task.task_id, entity.entity_uuid, 1, applied=True, changed=False),
            now=T0,
        )
        await outbox.purge_published(
            pg_session, older_than=timedelta(days=1), now=T0 + timedelta(days=2)
        )
    again = await record(pg_session, entity.entity_uuid, 1)
    assert (again.created, again.task.task_id, again.outbox_event) == (
        False,
        first.task.task_id,
        None,
    )
