"""Job queue §7.2: паралельні claimers, lease ownership, expiry recovery, idempotent enqueue,
max_attempts → dead letter (усі перевірки — за результатом, не за таймінгом: fake clock)."""

from __future__ import annotations

import asyncio
import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.errors import LeaseNotOwnedError
from collector.persistence.postgres.models import CrawlJob, DeadLetter
from collector.persistence.postgres.repositories import queue

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


async def _enqueue_many(sessions: async_sessionmaker[AsyncSession], count: int) -> list[UUID]:
    ids: list[UUID] = []
    async with sessions() as session, session.begin():
        for i in range(count):
            job = await queue.enqueue(
                session,
                queue.NewJob(job_type="fetch", idempotency_key=f"fetch:{i}", args={"n": i}),
                now=T0,
            )
            ids.append(job.job_id)
    return ids


async def test_four_parallel_claimers_claim_each_job_exactly_once(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_ids = await _enqueue_many(pg_sessions, 100)

    async def claimer(name: str) -> list[UUID]:
        claimed: list[UUID] = []
        async with pg_sessions() as session:
            while True:
                async with session.begin():
                    batch = await queue.claim(session, ["fetch"], name, 60, limit=3, now=T0)
                if not batch:
                    return claimed
                claimed.extend(job.job_id for job in batch)
                for job in batch:
                    assert job.lease_owner == name
                    assert job.attempt == 1

    results = await asyncio.gather(*(claimer(f"worker-{i}") for i in range(4)))
    counts = Counter(job_id for part in results for job_id in part)
    assert set(counts) == set(job_ids)
    assert all(count == 1 for count in counts.values()), counts.most_common(3)
    assert sum(len(part) for part in results) == 100

    async with pg_sessions() as session:
        leased = await session.scalar(
            select(func.count()).select_from(CrawlJob).where(CrawlJob.status == "leased")
        )
    assert leased == 100


async def test_enqueue_with_same_idempotency_key_returns_existing_job(
    pg_session: AsyncSession,
) -> None:
    async with pg_session.begin():
        first = await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="k1", priority=5), now=T0
        )
        second = await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="k1", priority=9), now=T0
        )
    assert first.job_id == second.job_id
    assert second.priority == 5  # перший запис незмінний
    async with pg_session.begin():
        total = await pg_session.scalar(select(func.count()).select_from(CrawlJob))
    assert total == 1


async def test_heartbeat_by_non_owner_is_rejected(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="hb"), now=T0
        )
        [job] = await queue.claim(pg_session, ["fetch"], "worker-a", 30, now=T0)
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.heartbeat(pg_session, job.job_id, "worker-b", 30, now=T0)
    async with pg_session.begin():
        expires = await queue.heartbeat(pg_session, job.job_id, "worker-a", 45, now=T0)
    assert expires == T0 + timedelta(seconds=45)
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.complete(pg_session, job.job_id, "worker-b", now=T0)


async def test_expired_lease_is_recovered_and_reclaimed_by_another_worker(
    pg_session: AsyncSession,
) -> None:
    async with pg_session.begin():
        await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="exp"), now=T0
        )
        [job] = await queue.claim(pg_session, ["fetch"], "worker-a", 10, now=T0)
    t_before_expiry = T0 + timedelta(seconds=9)
    t_after_expiry = T0 + timedelta(seconds=11)
    async with pg_session.begin():
        assert await queue.recover_expired_leases(pg_session, now=t_before_expiry) == []
        assert await queue.claim(pg_session, ["fetch"], "worker-b", 10, now=t_before_expiry) == []
    async with pg_session.begin():
        assert await queue.recover_expired_leases(pg_session, now=t_after_expiry) == [job.job_id]
    async with pg_session.begin():
        [reclaimed] = await queue.claim(pg_session, ["fetch"], "worker-b", 10, now=t_after_expiry)
    assert reclaimed.job_id == job.job_id
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.attempt == 2  # attempt збережено після recovery
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.complete(pg_session, job.job_id, "worker-a", now=t_after_expiry)


async def test_retry_backoff_then_max_attempts_quarantines_with_dead_letter(
    pg_session: AsyncSession,
) -> None:
    policy = queue.BackoffPolicy(base=timedelta(seconds=10), multiplier=2, jitter_ratio=0.0)
    async with pg_session.begin():
        await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="rt", max_attempts=3), now=T0
        )
    now = T0
    for attempt in (1, 2):
        async with pg_session.begin():
            [job] = await queue.claim(pg_session, ["fetch"], "w", 30, now=now)
            assert job.attempt == attempt
            job = await queue.retry(
                pg_session,
                job.job_id,
                "w",
                error_code="http_503",
                policy=policy,
                rng=random.Random(1),  # noqa: S311 — детермінований jitter у тесті
                now=now,
            )
        assert job.status == "retry"
        expected_delay = timedelta(seconds=10 * 2 ** (attempt - 1))
        assert job.not_before == now + expected_delay
        async with pg_session.begin():
            assert await queue.claim(pg_session, ["fetch"], "w", 30, now=now) == []
        now = job.not_before
    async with pg_session.begin():
        [job] = await queue.claim(pg_session, ["fetch"], "w", 30, now=now)
        assert job.attempt == 3
        job = await queue.retry(
            pg_session, job.job_id, "w", error_code="http_503", policy=policy, now=now
        )
    assert job.status == "quarantined"
    async with pg_session.begin():
        assert await queue.claim(pg_session, ["fetch"], "w", 30, now=now + timedelta(days=1)) == []
        letters = await queue.list_dead_letters(pg_session)
    assert [(dl.job_id, dl.reason, dl.attempt, dl.error_code) for dl in letters] == [
        (job.job_id, "max_attempts", 3, "http_503")
    ]


async def test_claim_respects_priority_not_before_and_job_type(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        low = await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="low", priority=1), now=T0
        )
        high = await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="high", priority=100), now=T0
        )
        await queue.enqueue(
            pg_session,
            queue.NewJob(
                job_type="fetch",
                idempotency_key="future",
                priority=200,
                not_before=T0 + timedelta(minutes=5),
            ),
            now=T0,
        )
        await queue.enqueue(pg_session, queue.NewJob(job_type="parse", idempotency_key="p"), now=T0)
    async with pg_session.begin():
        claimed = await queue.claim(pg_session, ["fetch"], "w", 30, limit=10, now=T0)
    assert [job.job_id for job in claimed] == [high.job_id, low.job_id]


async def test_operator_quarantine_and_complete_are_terminal(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        job = await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="q"), now=T0
        )
        job = await queue.quarantine(pg_session, job.job_id, None, error_code="manual", now=T0)
    assert job.status == "quarantined"
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.quarantine(pg_session, job.job_id, None, error_code="manual", now=T0)
        letters = await pg_session.scalars(
            select(DeadLetter).where(DeadLetter.job_id == job.job_id)
        )
        assert [dl.reason for dl in letters] == ["quarantine"]
    async with pg_session.begin():
        done = await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="d"), now=T0
        )
        [done] = await queue.claim(pg_session, ["fetch"], "w", 30, now=T0)
        done = await queue.complete(pg_session, done.job_id, "w", now=T0)
    assert done.status == "succeeded"
    assert done.lease_owner is None
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.heartbeat(pg_session, done.job_id, "w", 30, now=T0)
