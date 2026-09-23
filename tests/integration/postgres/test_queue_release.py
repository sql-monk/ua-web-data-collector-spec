"""`queue.release` (dependency WP-01D→WP-01A §3/§5; gate 3 CR-5): плановий drain компенсує
інкремент `attempt` від claim (`GREATEST(attempt - 1, 0)`),
без полів помилки і без карантину навіть на останній спробі."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.persistence.postgres.errors import LeaseNotOwnedError
from collector.persistence.postgres.models import CrawlJob, DeadLetter
from collector.persistence.postgres.repositories import queue

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


async def _leased_on_last_attempt(session: AsyncSession) -> CrawlJob:
    async with session.begin():
        await queue.enqueue(
            session, queue.NewJob(job_type="fetch", idempotency_key="k", max_attempts=1), now=T0
        )
        [job] = await queue.claim(session, ["fetch"], "w-1", 60, now=T0)
    assert (job.attempt, job.max_attempts) == (1, 1)
    return job


async def test_release_returns_job_immediately_without_attempt_or_error(
    pg_session: AsyncSession,
) -> None:
    job = await _leased_on_last_attempt(pg_session)
    async with pg_session.begin():
        released = await queue.release(pg_session, job.job_id, "w-1", now=T0 + timedelta(seconds=5))
    assert released.status == "pending"
    # Спроба, перервана drain, не «згоряє»: інкремент claim скасовано (CR-5).
    assert (released.attempt, released.lease_owner, released.lease_expires_at) == (0, None, None)
    assert (released.last_error_code, released.last_error_message) == (None, None)
    assert released.not_before == T0 + timedelta(seconds=5)
    async with pg_session.begin():
        # Claimable одразу — без очікування TTL lease, як було з recover_expired_leases.
        [again] = await queue.claim(pg_session, ["fetch"], "w-2", 60, now=T0 + timedelta(seconds=5))
        letters = await pg_session.scalar(select(func.count()).select_from(DeadLetter))
    assert again.job_id == job.job_id and again.lease_owner == "w-2"
    assert letters == 0, "плановий drain — не збій: dead letter не пишеться"


async def test_release_of_foreign_or_finished_lease_is_rejected(pg_session: AsyncSession) -> None:
    job = await _leased_on_last_attempt(pg_session)
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.release(pg_session, job.job_id, "w-other", now=T0)
        await queue.complete(pg_session, job.job_id, "w-1", now=T0)
        with pytest.raises(LeaseNotOwnedError):
            await queue.release(pg_session, job.job_id, "w-1", now=T0)
