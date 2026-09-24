"""PR3a — adversarial-сценарії незалежного тестувальника (testing-pr3a.md).

Доповнюють тести реалізатора межовими й конкурентними випадками:

- `not_before` у минулому / далекому майбутньому / з іншою tz / naive — для `queue.retry`,
  `queue.release`, `projection.retry_projection_task`, `projection.release_projection_task`;
- `release(not_before=)` не спалює спробу, не пише й не стирає поля помилки, не веде в dead
  letter навіть на межі `max_attempts`;
- конкурентні claim vs defer (два з'єднання, row locks);
- fencing ack: протермінований і відновлений lease, відсутній task → `LeaseNotOwnedError`
  без жодного запису;
- outbox N-2: межа `max_delivery_attempts=1`, конкурентні publisher-и не видають той самий
  рядок двічі в межах visibility lease, паркування рівно один раз;
- `purge_published`: строга межа cutoff, припаркований старий рядок, молодий acknowledged
  internal, конкурентні purge;
- лічильник збоїв route: rollback атомарний з audit, `degraded` → `circuit_open`, лічильник не
  робить revision застарілою, `reset` ідемпотентний;
- `count_retries_since`: межі вікна, партиції, tz;
- `ProjectionCompleteness.settled` vs `.complete`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts import new_entity_id
from collector.contracts.enums import FetchOutcome, RouteState
from collector.persistence.postgres.errors import LeaseNotOwnedError
from collector.persistence.postgres.models import (
    AuditLog,
    ChangeEvent,
    CrawlJob,
    DeadLetter,
    EntityIndex,
    OutboxEvent,
    ProjectionAcknowledgement,
    ProjectionTask,
    SourceRoute,
)
from collector.persistence.postgres.repositories import (
    artifacts,
    outbox,
    projection,
    queue,
    reconciliation,
    sources,
)

from .conftest import FIXED_NOW, make_entity, receipt, record
from .test_fetch_preflight import _fetch, seed_source
from .test_outbox_delivery import _acked_domain_event

pytestmark = pytest.mark.integration

T0 = FIXED_NOW
VISIBILITY = 60


# --- helpers -----------------------------------------------------------------------------


async def _leased_job(session: AsyncSession, *, max_attempts: int = 4, key: str = "adv") -> UUID:
    async with session.begin():
        await queue.enqueue(
            session,
            queue.NewJob(job_type="fetch", idempotency_key=key, max_attempts=max_attempts),
            now=T0,
        )
        [job] = await queue.claim(session, ["fetch"], "w-1", 60, now=T0)
        return job.job_id


async def _job(session: AsyncSession, job_id: UUID) -> CrawlJob:
    async with session.begin():
        job = await session.get(CrawlJob, job_id, populate_existing=True)
    assert job is not None
    return job


async def _task(session: AsyncSession, task_id: UUID) -> ProjectionTask:
    async with session.begin():
        task = await session.get(ProjectionTask, task_id, populate_existing=True)
    assert task is not None
    return task


async def _count(session: AsyncSession, model: type[object]) -> int:
    async with session.begin():
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _claimed_task(session: AsyncSession, item: str = "item-1", n: int = 1) -> UUID:
    entity = await make_entity(session, item)
    await record(session, entity.entity_uuid, n)
    async with session.begin():
        [task] = await projection.claim_projection_tasks(session, "p-1", 60, now=T0)
        return task.task_id


# --- not_before: past / far future / other tz / naive ------------------------------------

FAR_FUTURE = datetime(2999, 1, 1, tzinfo=UTC)
KYIV = timezone(timedelta(hours=3))


async def test_queue_far_future_not_before_is_stored_exactly_and_not_claimable(
    pg_session: AsyncSession,
) -> None:
    job_id = await _leased_job(pg_session)
    async with pg_session.begin():
        released = await queue.release(pg_session, job_id, "w-1", not_before=FAR_FUTURE, now=T0)
        assert released.not_before == FAR_FUTURE
        assert (
            await queue.claim(pg_session, ["fetch"], "w-2", 60, now=T0 + timedelta(days=3650)) == []
        )
    job_id2 = await _leased_job(pg_session, key="adv-2")
    async with pg_session.begin():
        retried = await queue.retry(
            pg_session, job_id2, "w-1", error_code="x", not_before=FAR_FUTURE, now=T0
        )
    assert retried.not_before == FAR_FUTURE


async def test_not_before_with_non_utc_offset_is_the_same_instant(
    pg_session: AsyncSession,
) -> None:
    job_id = await _leased_job(pg_session)
    local = datetime(2026, 9, 22, 15, 10, tzinfo=KYIV)  # = T0 + 10 хв UTC
    async with pg_session.begin():
        await queue.release(pg_session, job_id, "w-1", not_before=local, now=T0)
    job = await _job(pg_session, job_id)
    assert job.not_before == T0 + timedelta(minutes=10)
    async with pg_session.begin():
        assert (
            await queue.claim(pg_session, ["fetch"], "w-2", 60, now=T0 + timedelta(minutes=9)) == []
        )


async def test_naive_not_before_is_rejected_without_any_write(pg_session: AsyncSession) -> None:
    naive = datetime(2026, 9, 22, 13, 0)  # noqa: DTZ001 — саме naive і перевіряємо
    job_id = await _leased_job(pg_session)
    task_id = await _claimed_task(pg_session)
    calls = (
        lambda s: queue.release(s, job_id, "w-1", not_before=naive, now=T0),
        lambda s: queue.retry(s, job_id, "w-1", error_code="x", not_before=naive, now=T0),
        lambda s: projection.release_projection_task(s, task_id, "p-1", not_before=naive, now=T0),
        lambda s: projection.retry_projection_task(
            s, task_id, "p-1", error_code="x", not_before=naive, now=T0
        ),
    )
    for call in calls:
        with pytest.raises((TypeError, ValueError)):
            async with pg_session.begin():
                await call(pg_session)
    job = await _job(pg_session, job_id)
    assert (job.status, job.lease_owner, job.attempt, job.not_before) == ("leased", "w-1", 1, T0)
    assert job.last_error_code is None
    task = await _task(pg_session, task_id)
    assert (task.status, task.lease_owner, task.attempt) == ("leased", "p-1", 1)
    assert task.last_error_code is None


async def test_projection_release_and_retry_clamp_past_not_before(
    pg_session: AsyncSession,
) -> None:
    task_id = await _claimed_task(pg_session)
    async with pg_session.begin():
        released = await projection.release_projection_task(
            pg_session, task_id, "p-1", not_before=T0 - timedelta(days=1), now=T0
        )
    assert (released.status, released.not_before, released.attempt) == ("pending", T0, 0)
    async with pg_session.begin():
        [again] = await projection.claim_projection_tasks(pg_session, "p-1", 60, now=T0)
        retried = await projection.retry_projection_task(
            pg_session,
            again.task_id,
            "p-1",
            error_code="x",
            not_before=T0 - timedelta(days=1),
            now=T0,
        )
    assert (retried.status, retried.not_before) == ("retry", T0)


# --- release(not_before=) does not burn attempt / touch error fields ---------------------


async def test_defer_keeps_previous_error_fields_and_compensates_attempt(
    pg_session: AsyncSession,
) -> None:
    job_id = await _leased_job(pg_session, max_attempts=5)
    async with pg_session.begin():
        await queue.retry(
            pg_session,
            job_id,
            "w-1",
            error_code="http_503",
            error_message="boom",
            now=T0,
            not_before=T0,
        )
        [job] = await queue.claim(pg_session, ["fetch"], "w-1", 60, now=T0)
        assert job.attempt == 2
        deferred = await queue.release(
            pg_session, job_id, "w-1", not_before=T0 + timedelta(minutes=5), now=T0
        )
    assert (deferred.status, deferred.attempt) == ("pending", 1)
    # release не пише власну помилку і не стирає попередню (діагностика лишається).
    assert (deferred.last_error_code, deferred.last_error_message) == ("http_503", "boom")
    assert await _count(pg_session, DeadLetter) == 0


async def test_defer_at_max_attempts_does_not_dead_letter(pg_session: AsyncSession) -> None:
    job_id = await _leased_job(pg_session, max_attempts=1)  # attempt 1 == max
    async with pg_session.begin():
        deferred = await queue.release(
            pg_session, job_id, "w-1", not_before=T0 + timedelta(minutes=1), now=T0
        )
    assert (deferred.status, deferred.attempt) == ("pending", 0)
    async with pg_session.begin():
        [job] = await queue.claim(pg_session, ["fetch"], "w-1", 60, now=T0 + timedelta(minutes=1))
    assert (job.status, job.attempt) == ("leased", 1)
    assert await _count(pg_session, DeadLetter) == 0


async def test_projection_defer_keeps_error_fields_and_never_quarantines(
    pg_session: AsyncSession,
) -> None:
    task_id = await _claimed_task(pg_session)
    async with pg_session.begin():
        await projection.retry_projection_task(
            pg_session, task_id, "p-1", error_code="mongo_timeout", not_before=T0, now=T0
        )
    now = T0
    for _ in range(6):  # max_attempts за замовчуванням малий, defer-и його не з'їдають
        async with pg_session.begin():
            [task] = await projection.claim_projection_tasks(pg_session, "p-1", 60, now=now)
            now += timedelta(minutes=1)
            await projection.release_projection_task(
                pg_session, task.task_id, "p-1", not_before=now, now=now
            )
    task = await _task(pg_session, task_id)
    assert (task.status, task.attempt, task.last_error_code) == ("pending", 1, "mongo_timeout")


# --- concurrent claim vs defer (two connections) -----------------------------------------


async def test_uncommitted_defer_hides_job_from_concurrent_claim(
    pg_sessions: async_sessionmaker[AsyncSession], pg_session: AsyncSession
) -> None:
    job_id = await _leased_job(pg_session)
    until = T0 + timedelta(minutes=10)
    async with pg_sessions() as owner, pg_sessions() as rival:
        await owner.begin()
        await queue.release(owner, job_id, "w-1", not_before=until, now=T0)
        # Рядок під row lock незакоміченого defer: SKIP LOCKED, а не подвійна видача.
        async with rival.begin():
            assert await queue.claim(rival, ["fetch"], "w-2", 60, now=T0 + timedelta(hours=1)) == []
        await owner.commit()
        async with rival.begin():
            assert (
                await queue.claim(rival, ["fetch"], "w-2", 60, now=until - timedelta(seconds=1))
                == []
            )
            [job] = await queue.claim(rival, ["fetch"], "w-2", 60, now=until)
    assert (job.job_id, job.attempt) == (job_id, 1)


async def test_defer_by_stale_owner_waits_for_rival_claim_and_is_rejected(
    pg_sessions: async_sessionmaker[AsyncSession], pg_session: AsyncSession
) -> None:
    job_id = await _leased_job(pg_session)
    later = T0 + timedelta(minutes=5)
    async with pg_sessions() as rival, pg_sessions() as stale:
        await rival.begin()
        assert await queue.recover_expired_leases(rival, now=later) == [job_id]
        [job] = await queue.claim(rival, ["fetch"], "w-2", 60, now=later)
        assert job.job_id == job_id

        async def stale_defer() -> CrawlJob:
            async with stale.begin():
                return await queue.release(
                    stale, job_id, "w-1", not_before=later + timedelta(hours=1), now=later
                )

        pending = asyncio.create_task(stale_defer())
        await asyncio.sleep(0.5)
        assert not pending.done(), "defer старого власника має чекати на row lock"
        await rival.commit()
        with pytest.raises(LeaseNotOwnedError):
            await pending
    job_after = await _job(pg_session, job_id)
    assert (job_after.status, job_after.lease_owner, job_after.attempt) == ("leased", "w-2", 2)


# --- fencing ack: expired / recovered lease, missing task ----------------------------------


async def _ack_counts(session: AsyncSession) -> tuple[int, int, int]:
    return (
        await _count(session, ProjectionAcknowledgement),
        await _count(session, ChangeEvent),
        await _count(session, OutboxEvent) - 1,  # мінус internal projection.command
    )


async def test_ack_with_owner_after_recovery_is_rejected_without_any_write(
    pg_session: AsyncSession,
) -> None:
    task_id = await _claimed_task(pg_session)
    task = await _task(pg_session, task_id)
    entity_uuid = task.entity_uuid
    async with pg_session.begin():
        await projection.recover_expired_projection_leases(
            pg_session, now=T0 + timedelta(minutes=5)
        )
    applied = receipt(task_id, entity_uuid, 1, applied=True, changed=True)
    with pytest.raises(LeaseNotOwnedError):
        async with pg_session.begin():
            await projection.acknowledge_projection(
                pg_session, task_id, applied, owner="p-1", now=T0 + timedelta(minutes=5)
            )
    assert await _ack_counts(pg_session) == (0, 0, 0)
    task = await _task(pg_session, task_id)
    assert (task.status, task.lease_owner) == ("pending", None)
    async with pg_session.begin():
        entity = await pg_session.get(EntityIndex, entity_uuid, populate_existing=True)
    assert entity is not None and entity.confirmed_projection_version == 0


async def test_ack_with_owner_on_expired_but_unrecovered_lease_is_accepted(
    pg_session: AsyncSession,
) -> None:
    """Задокументована семантика (docstring `acknowledge_projection`, як `queue.complete`):
    протермінований, але ще не відновлений lease власник підтвердити може — ніхто інший
    task не тримає, а recover/claim серіалізуються тим самим row lock."""
    task_id = await _claimed_task(pg_session)
    task = await _task(pg_session, task_id)
    async with pg_session.begin():
        result = await projection.acknowledge_projection(
            pg_session,
            task_id,
            receipt(task_id, task.entity_uuid, 1, applied=True, changed=True),
            owner="p-1",
            now=T0 + timedelta(hours=1),
        )
    assert result.created


async def test_ack_with_owner_for_missing_task_is_rejected(pg_session: AsyncSession) -> None:
    missing = new_entity_id()
    with pytest.raises(LeaseNotOwnedError):
        async with pg_session.begin():
            await projection.acknowledge_projection(
                pg_session,
                missing,
                receipt(missing, new_entity_id(), 1, applied=True, changed=True),
                owner="p-1",
                now=T0,
            )
    assert await _count(pg_session, ProjectionAcknowledgement) == 0


# --- outbox N-2: boundary and concurrent publishers ----------------------------------------


async def test_max_delivery_attempts_one_parks_on_second_hand_out(
    pg_session: AsyncSession,
) -> None:
    _, event_id = await _acked_domain_event(pg_session, 40)
    async with pg_session.begin():
        [first] = await outbox.fetch_unpublished(
            pg_session, visibility_seconds=VISIBILITY, max_delivery_attempts=1, now=T0
        )
        assert first.delivery_attempts == 1 and first.parked_at is None
    later = T0 + timedelta(seconds=VISIBILITY)
    async with pg_session.begin():
        assert (
            await outbox.fetch_unpublished(
                pg_session, visibility_seconds=VISIBILITY, max_delivery_attempts=1, now=later
            )
            == []
        )
        row = await pg_session.get(OutboxEvent, event_id, populate_existing=True)
    assert row is not None
    assert (row.delivery_attempts, row.parked_at, row.last_error_code) == (
        1,
        later,
        outbox.DELIVERY_ATTEMPTS_EXHAUSTED,
    )


async def test_raising_the_limit_later_does_not_resurrect_parked_row(
    pg_session: AsyncSession,
) -> None:
    await _acked_domain_event(pg_session, 41)
    now = T0
    for _ in range(2):
        async with pg_session.begin():
            await outbox.fetch_unpublished(pg_session, max_delivery_attempts=1, now=now)
        now += timedelta(seconds=VISIBILITY)
    async with pg_session.begin():
        assert await outbox.fetch_unpublished(pg_session, max_delivery_attempts=100, now=now) == []


async def test_concurrent_publishers_never_hand_out_the_same_row_within_lease(
    pg_sessions: async_sessionmaker[AsyncSession], pg_session: AsyncSession
) -> None:
    events = {(await _acked_domain_event(pg_session, 50 + n))[1] for n in range(10)}
    max_attempts = 3

    async def drain(now: datetime) -> list[UUID]:
        taken: list[UUID] = []
        async with pg_sessions() as session:
            while True:
                async with session.begin():
                    batch = await outbox.fetch_unpublished(
                        session,
                        limit=2,
                        visibility_seconds=VISIBILITY,
                        max_delivery_attempts=max_attempts,
                        now=now,
                    )
                if not batch:
                    return taken
                taken.extend(event.outbox_id for event in batch)
                await asyncio.sleep(0)

    now = T0
    for round_no in range(1, max_attempts + 1):
        results = await asyncio.gather(*(drain(now) for _ in range(4)))
        flat = [outbox_id for batch in results for outbox_id in batch]
        assert len(flat) == len(set(flat)), f"раунд {round_no}: рядок видано двічі"
        assert set(flat) == events
        # Ще в межах visibility lease — ніхто нічого не отримує.
        assert await drain(now + timedelta(seconds=VISIBILITY - 1)) == []
        async with pg_session.begin():
            counters = (
                (
                    await pg_session.execute(
                        select(OutboxEvent.delivery_attempts).where(OutboxEvent.topic == "domain")
                    )
                )
                .scalars()
                .all()
            )
        assert set(counters) == {round_no}
        now += timedelta(seconds=VISIBILITY)
    # Межа: паралельні publisher-и паркують кожен рядок рівно один раз і нічого не видають.
    # Один виклик паркує щонайбільше `limit` рядків і повертає [] (див. тест нижче), тому
    # кілька хвиль, доки backlog не спорожніє.
    for _ in range(5):
        results = await asyncio.gather(*(drain(now) for _ in range(4)))
        assert all(batch == [] for batch in results)
    async with pg_session.begin():
        assert await outbox.count_backlog(pg_session, topic="domain") == 0
        parked = await outbox.list_parked(pg_session, limit=100)
    assert {event.outbox_id for event in parked} == events
    assert {(event.parked_at, event.delivery_attempts) for event in parked} == {(now, max_attempts)}


async def test_batch_of_only_exhausted_rows_returns_empty_while_backlog_remains(
    pg_session: AsyncSession,
) -> None:
    """Фіксує поведінку (знахідка low у testing-pr3a.md): `[]` від `fetch_unpublished` не
    означає «backlog порожній» — батч, де всі вибрані рядки вичерпали ліміт, лише паркує їх."""
    for n in range(3):
        await _acked_domain_event(pg_session, 45 + n)
    async with pg_session.begin():
        assert len(await outbox.fetch_unpublished(pg_session, max_delivery_attempts=1, now=T0)) == 3
    later = T0 + timedelta(seconds=VISIBILITY)
    async with pg_session.begin():
        assert (
            await outbox.fetch_unpublished(pg_session, limit=2, max_delivery_attempts=1, now=later)
            == []
        )
        assert await outbox.count_backlog(pg_session, topic="domain") == 1
    async with pg_session.begin():
        assert (
            await outbox.fetch_unpublished(pg_session, limit=2, max_delivery_attempts=1, now=later)
            == []
        )
        assert await outbox.count_backlog(pg_session, topic="domain") == 0
        assert len(await outbox.list_parked(pg_session)) == 3


# --- purge_published -----------------------------------------------------------------------


async def test_purge_cutoff_is_strict_and_parked_or_young_rows_stay(
    pg_session: AsyncSession,
) -> None:
    _, at_cutoff = await _acked_domain_event(pg_session, 60)
    _, before_cutoff = await _acked_domain_event(pg_session, 61)
    _, parked = await _acked_domain_event(pg_session, 62)
    older_than = timedelta(days=7)
    now = T0 + timedelta(days=30)
    cutoff = now - older_than
    async with pg_session.begin():
        assert await outbox.mark_published(pg_session, [at_cutoff], now=cutoff) == 1
        assert (
            await outbox.mark_published(
                pg_session, [before_cutoff], now=cutoff - timedelta(microseconds=1)
            )
            == 1
        )
        await outbox.fetch_unpublished(pg_session, max_delivery_attempts=1, now=T0)
        await outbox.fetch_unpublished(
            pg_session, max_delivery_attempts=1, now=T0 + timedelta(minutes=5)
        )
        row = await pg_session.get(OutboxEvent, parked, populate_existing=True)
        assert row is not None and row.parked_at is not None
    # Молодий acknowledged internal (створений після cutoff) — теж лишається.
    young_entity = await make_entity(pg_session, "young")
    young = (await record(pg_session, young_entity.entity_uuid, 63)).task
    async with pg_session.begin():
        await pg_session.execute(
            OutboxEvent.__table__.update()
            .where(OutboxEvent.event_id == young.task_id)
            .values(created_at=cutoff + timedelta(seconds=1))
        )
        await projection.acknowledge_projection(
            pg_session,
            young.task_id,
            receipt(young.task_id, young_entity.entity_uuid, 1, applied=True, changed=False),
            now=T0,
        )
    async with pg_session.begin():
        result = await outbox.purge_published(pg_session, older_than=older_than, now=now)
        remaining = set((await pg_session.execute(select(OutboxEvent.outbox_id))).scalars().all())
        young_row = await outbox.get_event(pg_session, young.task_id)
    assert before_cutoff not in remaining
    assert at_cutoff in remaining, "published_at == cutoff: межа строга (<)"
    assert parked in remaining
    assert young_row is not None
    # 3 acknowledged internal-команди (60, 61, 62) + 1 domain (before_cutoff).
    assert result.deleted == 4


async def test_concurrent_purges_do_not_double_delete_or_fail(
    pg_sessions: async_sessionmaker[AsyncSession], pg_session: AsyncSession
) -> None:
    published: list[UUID] = []
    for n in range(6):
        published.append((await _acked_domain_event(pg_session, 70 + n))[1])
    async with pg_session.begin():
        await outbox.mark_published(pg_session, published, now=T0)
    now = T0 + timedelta(days=2)

    async def purge_all() -> int:
        total = 0
        after: UUID | None = None
        async with pg_sessions() as session:
            while True:
                async with session.begin():
                    result = await outbox.purge_published(
                        session, older_than=timedelta(days=1), after=after, limit=2, now=now
                    )
                if result.last_event_id is None:
                    return total
                total += result.deleted
                after = result.last_event_id

    totals = await asyncio.gather(*(purge_all() for _ in range(3)))
    assert sum(totals) == 12  # 6 domain + 6 acknowledged internal
    assert await _count(pg_session, OutboxEvent) == 0


# --- route failure counter -----------------------------------------------------------------


async def test_rolled_back_route_failure_leaves_no_counter_state_or_audit(
    pg_session: AsyncSession,
) -> None:
    _, route = await seed_source(pg_session)
    route_id = route.id
    with pytest.raises(RuntimeError):
        async with pg_session.begin():
            state = await sources.record_route_failure(
                pg_session, route_id, actor="w", reason="http_503", threshold=1, now=T0
            )
            assert state is RouteState.CIRCUIT_OPEN
            raise RuntimeError("fetch transaction aborted")
    async with pg_session.begin():
        row = await pg_session.get(SourceRoute, route_id, populate_existing=True)
        audits = await pg_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_id == str(route_id),
                AuditLog.action == "source_route.circuit_open",
            )
        )
    assert row is not None
    assert (row.consecutive_failures, row.state, row.last_failure_at) == (0, "healthy", None)
    assert audits == 0


async def test_sub_threshold_failures_do_not_stale_revision_and_reset_is_idempotent(
    pg_session: AsyncSession,
) -> None:
    _, route = await seed_source(pg_session)
    route_id, revision = route.id, route.revision
    async with pg_session.begin():
        for _ in range(2):
            await sources.record_route_failure(
                pg_session, route_id, actor="w", reason="timeout", threshold=5, now=T0
            )
        for _ in range(2):
            assert (
                await sources.reset_route_failures(pg_session, route_id, now=T0)
                is RouteState.HEALTHY
            )
    async with pg_session.begin():
        # Оператор з відкритою до збоїв формою (стара revision) не отримує StaleRevision.
        degraded = await sources.set_route_state(
            pg_session,
            route_id,
            RouteState.DEGRADED,
            expected_revision=revision,
            actor="op",
            reason="slow",
            now=T0,
        )
        degraded_revision = degraded.revision
    async with pg_session.begin():
        state = await sources.record_route_failure(
            pg_session, route_id, actor="w", reason="timeout", threshold=1, now=T0
        )
        row = await pg_session.get(SourceRoute, route_id, populate_existing=True)
    assert state is RouteState.CIRCUIT_OPEN, "degraded теж відкривається лічильником"
    assert row is not None and row.revision == degraded_revision + 1


async def test_concurrent_failures_crossing_small_threshold_open_exactly_once(
    pg_sessions: async_sessionmaker[AsyncSession], pg_session: AsyncSession
) -> None:
    _, route = await seed_source(pg_session)
    route_id, revision = route.id, route.revision

    async def fail_once() -> RouteState:
        async with pg_sessions() as session, session.begin():
            return await sources.record_route_failure(
                session, route_id, actor="w", reason="timeout", threshold=2, now=T0
            )

    states = await asyncio.gather(*(fail_once() for _ in range(12)))
    async with pg_session.begin():
        row = await pg_session.get(SourceRoute, route_id, populate_existing=True)
        audits = await pg_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_id == str(route_id),
                AuditLog.action == "source_route.circuit_open",
            )
        )
    assert row is not None
    assert (row.consecutive_failures, row.state, row.revision) == (12, "circuit_open", revision + 1)
    assert audits == 1
    assert states.count(RouteState.HEALTHY) == 1


# --- count_retries_since: window boundaries -------------------------------------------------


async def test_count_retries_since_window_bounds_partitions_and_timezones(
    pg_session: AsyncSession,
) -> None:
    source, _ = await seed_source(pg_session)
    since = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)  # серпень → default-партиція
    for at in (
        since - timedelta(microseconds=1),  # поза вікном
        since,  # межа включна
        datetime(2026, 9, 1, tzinfo=UTC),  # вересневa партиція
        T0,
    ):
        await _fetch(pg_session, source.id, at, status=503, outcome=FetchOutcome.RETRYABLE)
    async with pg_session.begin():
        assert await artifacts.count_retries_since(pg_session, source.id, since) == 3
        assert (
            await artifacts.count_retries_since(pg_session, source.id, since.astimezone(KYIV)) == 3
        )
        assert (
            await artifacts.count_retries_since(
                pg_session, source.id, since + timedelta(microseconds=1)
            )
            == 2
        )
        assert (
            await artifacts.count_retries_since(pg_session, source.id, T0 + timedelta(seconds=1))
            == 0
        )


# --- settled vs complete -------------------------------------------------------------------


async def test_settled_and_complete_flags_follow_their_documented_meaning(
    pg_session: AsyncSession,
) -> None:
    async with pg_session.begin():
        empty = await reconciliation.projection_completeness(pg_session)
    assert (empty.settled, empty.complete) == (True, True)

    entity = await make_entity(pg_session)
    open_task = (await record(pg_session, entity.entity_uuid, 80)).task
    async with pg_session.begin():
        opened = await reconciliation.projection_completeness(
            pg_session, entity_uuid=entity.entity_uuid
        )
    assert (opened.settled, opened.complete) == (False, False)

    async with pg_session.begin():
        await projection.quarantine_projection_task(
            pg_session, open_task.task_id, error_code="bad", now=T0
        )
        quarantined = await reconciliation.projection_completeness(
            pg_session, entity_uuid=entity.entity_uuid
        )
    # §7.3 крок 5: quarantined = done → settled; картка п.9: блокує повноту → not complete.
    assert (quarantined.settled, quarantined.complete) == (True, False)

    other = await make_entity(pg_session, "other")
    acked = (await record(pg_session, other.entity_uuid, 81)).task
    async with pg_session.begin():
        await projection.acknowledge_projection(
            pg_session,
            acked.task_id,
            receipt(acked.task_id, other.entity_uuid, 1, applied=True, changed=True),
            now=T0,
        )
        done = await reconciliation.projection_completeness(
            pg_session, entity_uuid=other.entity_uuid
        )
    # Неопублікований domain.changed не впливає ні на settled, ні на complete.
    assert done.unpublished_domain_events == 1
    assert (done.settled, done.complete) == (True, True)

    later_task = (await record(pg_session, other.entity_uuid, 82)).task
    async with pg_session.begin():
        await pg_session.execute(
            ProjectionTask.__table__.update()
            .where(ProjectionTask.task_id == later_task.task_id)
            .values(created_at=T0 + timedelta(hours=1))
        )
        watermarked = await reconciliation.projection_completeness(
            pg_session, entity_uuid=other.entity_uuid, created_before=T0 + timedelta(minutes=1)
        )
        unbounded = await reconciliation.projection_completeness(
            pg_session, entity_uuid=other.entity_uuid
        )
    assert (watermarked.settled, watermarked.complete) == (True, True)
    assert (unbounded.settled, unbounded.complete, unbounded.open_tasks) == (False, False, 1)
