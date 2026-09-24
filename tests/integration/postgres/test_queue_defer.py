"""PR3a п.1: `not_before` у `retry` і defer через `release` — для `crawl_jobs` і
`projection_tasks` (WP-01D PR1c п.1–2, WP-02 п.5, WP-04 п.2в).

- `release(not_before=+10 хв)` → `pending`, `attempt` без змін (компенсація claim), поля
  помилки не чіпаються, dead letter немає, job не claim-иться раніше `not_before`;
- багато defer поспіль на job з малим `max_attempts` — жодного карантину;
- `retry(not_before=+2 год)` → рівно `+2 год`, дефолтний `BackoffPolicy` не додається;
  минуле `not_before` стискається до `now`; `max_attempts` усе одно веде в карантин.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.persistence.postgres.errors import LeaseNotOwnedError
from collector.persistence.postgres.models import CrawlJob, DeadLetter, ProjectionTask
from collector.persistence.postgres.repositories import projection, queue

from .conftest import FIXED_NOW, make_entity, record

pytestmark = pytest.mark.integration

T0 = FIXED_NOW


async def _leased(session: AsyncSession, *, max_attempts: int = 4) -> CrawlJob:
    async with session.begin():
        await queue.enqueue(
            session,
            queue.NewJob(job_type="fetch", idempotency_key="k", max_attempts=max_attempts),
            now=T0,
        )
        [job] = await queue.claim(session, ["fetch"], "w-1", 60, now=T0)
    return job


async def _dead_letters(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(DeadLetter)) or 0)


async def test_release_with_not_before_defers_without_burning_attempt(
    pg_session: AsyncSession,
) -> None:
    job = await _leased(pg_session)
    until = T0 + timedelta(minutes=10)
    async with pg_session.begin():
        deferred = await queue.release(pg_session, job.job_id, "w-1", not_before=until, now=T0)
    assert deferred.status == "pending"
    assert (deferred.attempt, deferred.not_before) == (0, until)
    assert (deferred.last_error_code, deferred.last_error_message) == (None, None)
    async with pg_session.begin():
        early = await queue.claim(
            pg_session, ["fetch"], "w-2", 60, now=until - timedelta(seconds=1)
        )
        assert early == [], "defer: job не claim-иться раніше not_before"
        [again] = await queue.claim(pg_session, ["fetch"], "w-2", 60, now=until)
        assert again.job_id == job.job_id and again.attempt == 1
        assert await _dead_letters(pg_session) == 0


async def test_many_defers_in_a_row_never_quarantine(pg_session: AsyncSession) -> None:
    job = await _leased(pg_session, max_attempts=2)
    now = T0
    for _ in range(5):
        async with pg_session.begin():
            now += timedelta(minutes=1)
            released = await queue.release(
                pg_session, job.job_id, "w-1", not_before=now + timedelta(seconds=30), now=now
            )
            assert released.status == "pending"
            now += timedelta(seconds=30)
            [job] = await queue.claim(pg_session, ["fetch"], "w-1", 60, now=now)
    assert (job.attempt, job.status) == (1, "leased")
    async with pg_session.begin():
        assert await _dead_letters(pg_session) == 0


async def test_release_with_past_not_before_is_clamped_to_now(pg_session: AsyncSession) -> None:
    job = await _leased(pg_session)
    async with pg_session.begin():
        released = await queue.release(
            pg_session, job.job_id, "w-1", not_before=T0 - timedelta(hours=1), now=T0
        )
    assert released.not_before == T0


async def test_retry_with_not_before_uses_exactly_that_bound(pg_session: AsyncSession) -> None:
    job = await _leased(pg_session)
    until = T0 + timedelta(hours=2)
    async with pg_session.begin():
        retried = await queue.retry(
            pg_session, job.job_id, "w-1", error_code="http_429", not_before=until, now=T0
        )
    assert retried.status == "retry"
    # Рівно задана межа: дефолтний BackoffPolicy (30 с + jitter) поверх не додається.
    assert retried.not_before == until
    assert retried.last_error_code == "http_429"


async def test_retry_with_short_table_delay_is_not_overridden_by_default_backoff(
    pg_session: AsyncSession,
) -> None:
    job = await _leased(pg_session)
    async with pg_session.begin():
        retried = await queue.retry(
            pg_session,
            job.job_id,
            "w-1",
            error_code="timeout",
            not_before=T0 + timedelta(seconds=5),
            now=T0,
        )
    assert retried.not_before == T0 + timedelta(seconds=5)


async def test_retry_with_past_not_before_is_clamped_and_max_attempts_still_quarantines(
    pg_session: AsyncSession,
) -> None:
    job = await _leased(pg_session, max_attempts=2)
    async with pg_session.begin():
        first = await queue.retry(
            pg_session,
            job.job_id,
            "w-1",
            error_code="timeout",
            not_before=T0 - timedelta(minutes=5),
            now=T0,
        )
        assert first.not_before == T0
        [job] = await queue.claim(pg_session, ["fetch"], "w-1", 60, now=T0)
        last = await queue.retry(
            pg_session,
            job.job_id,
            "w-1",
            error_code="timeout",
            not_before=T0 + timedelta(hours=1),
            now=T0,
        )
    assert last.status == "quarantined"
    async with pg_session.begin():
        assert await _dead_letters(pg_session) == 1


async def test_retry_without_not_before_keeps_backoff_policy(pg_session: AsyncSession) -> None:
    job = await _leased(pg_session)
    policy = queue.BackoffPolicy(base=timedelta(seconds=30), jitter_ratio=0.0)
    async with pg_session.begin():
        retried = await queue.retry(
            pg_session, job.job_id, "w-1", error_code="timeout", policy=policy, now=T0
        )
    assert retried.not_before == T0 + timedelta(seconds=30)


# --- projection_tasks: ті самі параметри ------------------------------------------------


async def _leased_task(session: AsyncSession) -> ProjectionTask:
    entity = await make_entity(session)
    await record(session, entity.entity_uuid, 1)
    async with session.begin():
        [task] = await projection.claim_projection_tasks(session, "p-1", 60, now=T0)
    return task


async def test_release_projection_task_with_not_before_defers(pg_session: AsyncSession) -> None:
    task = await _leased_task(pg_session)
    until = T0 + timedelta(minutes=10)
    async with pg_session.begin():
        deferred = await projection.release_projection_task(
            pg_session, task.task_id, "p-1", not_before=until, now=T0
        )
    assert (deferred.status, deferred.attempt, deferred.not_before) == ("pending", 0, until)
    assert deferred.last_error_code is None
    async with pg_session.begin():
        assert (
            await projection.claim_projection_tasks(
                pg_session, "p-2", 60, now=until - timedelta(seconds=1)
            )
            == []
        )
        [again] = await projection.claim_projection_tasks(pg_session, "p-2", 60, now=until)
    assert again.task_id == task.task_id


async def test_retry_projection_task_with_not_before_uses_exact_bound(
    pg_session: AsyncSession,
) -> None:
    task = await _leased_task(pg_session)
    until = T0 + timedelta(hours=2)
    async with pg_session.begin():
        retried = await projection.retry_projection_task(
            pg_session, task.task_id, "p-1", error_code="mongo_timeout", not_before=until, now=T0
        )
    assert (retried.status, retried.not_before) == ("retry", until)


async def test_release_and_retry_of_foreign_lease_are_rejected(pg_session: AsyncSession) -> None:
    task = await _leased_task(pg_session)
    job = await _leased(pg_session)
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.release(
                pg_session, job.job_id, "other", not_before=T0 + timedelta(hours=1), now=T0
            )
        with pytest.raises(LeaseNotOwnedError):
            await projection.release_projection_task(
                pg_session, task.task_id, "other", not_before=T0 + timedelta(hours=1), now=T0
            )
