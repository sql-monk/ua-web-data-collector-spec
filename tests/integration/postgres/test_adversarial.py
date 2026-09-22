"""Adversarial-сценарії WP-01A PR1 (незалежний прогін wp-tester).

Черга: heartbeat після закінчення lease, `complete` чужого/подвійний, retry після
`max_attempts` (рівно один dead letter), claim із порожнім списком типів, паралельний
`enqueue` того самого idempotency key з двох сесій.

Limiter: подвійний release, release невідомого permit, `block_origin` під час активних
permits, поповнення після довгої паузи не перевищує burst, незалежність origins,
звільнення concurrency slot при expiry після витраченого rate token.

Crawl runs: два паралельні `start_run(full)`.

Pools: stale/чужа revision без часткових записів, недозволені переходи, термінальні стани.

Migrations: `upgrade head` двічі поспіль ідемпотентний.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from collector.contracts import new_entity_id
from collector.contracts.enums import DataDomain
from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.errors import (
    ConflictError,
    InvalidTransitionError,
    LeaseNotOwnedError,
    NotFoundError,
    StaleRevisionError,
)
from collector.persistence.postgres.migrations import (
    check_no_drift,
    current_revision,
    upgrade_to_head,
)
from collector.persistence.postgres.models import (
    AuditLog,
    CrawlJob,
    CrawlRun,
    DeadLetter,
    ScaleCommand,
)
from collector.persistence.postgres.repositories import crawl_runs, limiter, pools, queue, sources
from collector.workers.roles import WorkerRole

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
ORIGIN_A = "https://a.test"
ORIGIN_B = "https://b.test"


# --------------------------------------------------------------------------- queue


async def test_heartbeat_after_lease_expiry_contract(pg_session: AsyncSession) -> None:
    """Контракт lease: поки прострочений lease не відновлено/не перехоплено, власник ще може
    продовжити heartbeat; після `recover_expired_leases` — `LeaseNotOwnedError` (потрібен
    повторний claim). Тест фіксує саме цей контракт, щоб він не змінився мовчки."""
    async with pg_session.begin():
        await queue.enqueue(
            pg_session, queue.NewJob(job_type="fetch", idempotency_key="hb"), now=T0
        )
        [job] = await queue.claim(pg_session, ["fetch"], "worker-a", 10, now=T0)
    after_expiry = T0 + timedelta(seconds=30)

    async with pg_session.begin():
        extended = await queue.heartbeat(pg_session, job.job_id, "worker-a", 10, now=after_expiry)
    assert extended == after_expiry + timedelta(seconds=10)

    # Чужий worker не може heartbeat-нути прострочений lease, доки його не відновлено.
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.heartbeat(pg_session, job.job_id, "worker-b", 10, now=after_expiry)

    recovered_at = after_expiry + timedelta(seconds=60)
    async with pg_session.begin():
        assert await queue.recover_expired_leases(pg_session, now=recovered_at) == [job.job_id]
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.heartbeat(pg_session, job.job_id, "worker-a", 10, now=recovered_at)
    async with pg_session.begin():
        refreshed = await queue.get_job(pg_session, job.job_id)
    assert refreshed is not None
    assert (refreshed.status, refreshed.lease_owner, refreshed.attempt) == ("pending", None, 1)


async def test_complete_by_foreign_worker_and_double_complete_are_rejected(
    pg_session: AsyncSession,
) -> None:
    async with pg_session.begin():
        await queue.enqueue(pg_session, queue.NewJob(job_type="fetch", idempotency_key="c"), now=T0)
        [job] = await queue.claim(pg_session, ["fetch"], "worker-a", 60, now=T0)
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.complete(pg_session, job.job_id, "worker-b", now=T0)
    async with pg_session.begin():
        done = await queue.complete(pg_session, job.job_id, "worker-a", now=T0)
    assert done.status == "succeeded"
    async with pg_session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue.complete(pg_session, job.job_id, "worker-a", now=T0)
    async with pg_session.begin():
        still = await queue.get_job(pg_session, job.job_id)
        letters = await queue.list_dead_letters(pg_session)
    assert still is not None and still.status == "succeeded"
    assert letters == []


async def test_retry_after_max_attempts_writes_exactly_one_dead_letter(
    pg_session: AsyncSession,
) -> None:
    """`max_attempts=1`: перший retry → quarantined + 1 dead letter; будь-який наступний
    retry/quarantine того самого job не додає другого запису."""
    async with pg_session.begin():
        await queue.enqueue(
            pg_session,
            queue.NewJob(job_type="fetch", idempotency_key="dl", max_attempts=1),
            now=T0,
        )
        [job] = await queue.claim(pg_session, ["fetch"], "w", 30, now=T0)
    assert job.attempt == 1
    async with pg_session.begin():
        quarantined = await queue.retry(pg_session, job.job_id, "w", error_code="http_500", now=T0)
    assert quarantined.status == "quarantined"

    for _ in range(3):
        async with pg_session.begin():
            with pytest.raises(LeaseNotOwnedError):
                await queue.retry(pg_session, job.job_id, "w", error_code="http_500", now=T0)
        async with pg_session.begin():
            with pytest.raises(LeaseNotOwnedError):
                await queue.quarantine(pg_session, job.job_id, None, error_code="manual", now=T0)
    async with pg_session.begin():
        letters = await pg_session.scalars(
            select(DeadLetter).where(DeadLetter.job_id == job.job_id)
        )
        rows = [(dl.reason, dl.attempt) for dl in letters]
    assert rows == [("max_attempts", 1)]


async def test_claim_with_empty_job_types_claims_nothing(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        await queue.enqueue(pg_session, queue.NewJob(job_type="fetch", idempotency_key="e"), now=T0)
    async with pg_session.begin():
        assert await queue.claim(pg_session, [], "w", 30, limit=10, now=T0) == []
    async with pg_session.begin():
        pending = await pg_session.scalar(
            select(func.count()).select_from(CrawlJob).where(CrawlJob.status == "pending")
        )
    assert pending == 1


async def test_priority_wins_for_identical_not_before(pg_session: AsyncSession) -> None:
    """Однаковий `not_before` → порядок визначає лише priority (спадно), далі job_id."""
    async with pg_session.begin():
        created = [
            await queue.enqueue(
                pg_session,
                queue.NewJob(job_type="fetch", idempotency_key=f"p{p}", priority=p, not_before=T0),
                now=T0,
            )
            for p in (10, 900, 500, 1, 700)
        ]
    expected = [job.job_id for job in sorted(created, key=lambda j: (-j.priority, j.job_id))]
    async with pg_session.begin():
        claimed = await queue.claim(pg_session, ["fetch"], "w", 30, limit=5, now=T0)
    assert [job.job_id for job in claimed] == expected


async def test_concurrent_enqueue_of_same_key_yields_one_row_without_unique_violation(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Шість сесій ставлять job з тим самим idempotency key з перекриттям транзакцій: назовні
    жодного `UniqueViolation`, у таблиці рівно один рядок, усі бачать той самий job_id."""

    async def producer(index: int) -> UUID:
        async with pg_sessions() as session, session.begin():
            job = await queue.enqueue(
                session,
                queue.NewJob(job_type="fetch", idempotency_key="race", args={"by": index}),
                now=T0,
            )
            # Тримаємо транзакцію відкритою, щоб решта сесій справді конкурували за ключ.
            await asyncio.sleep(0.25)
            return job.job_id

    job_ids = await asyncio.gather(*(producer(i) for i in range(6)))
    assert len(set(job_ids)) == 1, job_ids
    async with pg_sessions() as session:
        total = await session.scalar(select(func.count()).select_from(CrawlJob))
        stored = await session.scalar(select(CrawlJob.job_id))
    assert total == 1
    assert stored == job_ids[0]


# --------------------------------------------------------------------------- limiter


async def _bucket(
    session: AsyncSession, origin: str, *, capacity: int, rps: str, concurrency: int
) -> None:
    async with session.begin():
        await limiter.ensure_bucket(
            session,
            origin,
            capacity_tokens=capacity,
            refill_per_second=rps,
            max_concurrency=concurrency,
            now=T0,
        )


async def test_release_of_unknown_permit_is_false_not_error(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        assert await limiter.release_permit(pg_session, new_entity_id(), now=T0) is False
    # Транзакція не зіпсована — після «промаху» можна продовжувати працювати.
    await _bucket(pg_session, ORIGIN_A, capacity=5, rps="5", concurrency=1)
    async with pg_session.begin():
        decision = await limiter.acquire_permit(pg_session, ORIGIN_A, "a", 30, now=T0)
    assert decision.granted


async def test_double_release_is_idempotent_and_frees_slot_once(pg_session: AsyncSession) -> None:
    await _bucket(pg_session, ORIGIN_A, capacity=10, rps="10", concurrency=1)
    async with pg_session.begin():
        first = await limiter.acquire_permit(pg_session, ORIGIN_A, "a", 30, now=T0)
    assert first.granted and first.permit is not None
    async with pg_session.begin():
        assert await limiter.release_permit(pg_session, first.permit.permit_id, now=T0) is True
        assert await limiter.release_permit(pg_session, first.permit.permit_id, now=T0) is False
        assert await limiter.release_permit(pg_session, first.permit.permit_id, now=T0) is False
        assert await limiter.live_permit_count(pg_session, ORIGIN_A, now=T0) == 0
    # Повторний release не «створив» додаткового слоту: concurrency=1 далі діє.
    async with pg_session.begin():
        second = await limiter.acquire_permit(pg_session, ORIGIN_A, "b", 30, now=T0)
        third = await limiter.acquire_permit(pg_session, ORIGIN_A, "c", 30, now=T0)
    assert second.granted
    assert not third.granted and third.reason == "concurrency"


async def test_block_origin_with_live_permits_denies_new_but_keeps_existing(
    pg_session: AsyncSession,
) -> None:
    await _bucket(pg_session, ORIGIN_A, capacity=10, rps="10", concurrency=3)
    async with pg_session.begin():
        held = await limiter.acquire_permit(pg_session, ORIGIN_A, "a", 120, now=T0)
    assert held.granted and held.permit is not None
    until = T0 + timedelta(seconds=60)
    async with pg_session.begin():
        await limiter.block_origin(pg_session, ORIGIN_A, until, reason="429", now=T0)
    async with pg_session.begin():
        denied = await limiter.acquire_permit(pg_session, ORIGIN_A, "b", 30, now=T0)
        assert denied.reason == "blocked" and denied.retry_after == until
        # Активний permit не відкликано і його release приймається під час блокування.
        assert await limiter.live_permit_count(pg_session, ORIGIN_A, now=T0) == 1
        assert await limiter.release_permit(pg_session, held.permit.permit_id, now=T0) is True
        assert await limiter.live_permit_count(pg_session, ORIGIN_A, now=T0) == 0
        still_blocked = await limiter.acquire_permit(pg_session, ORIGIN_A, "b", 30, now=T0)
    assert still_blocked.reason == "blocked"
    async with pg_session.begin():
        after = await limiter.acquire_permit(pg_session, ORIGIN_A, "b", 30, now=until)
    assert after.granted


async def test_long_pause_refill_never_exceeds_burst_capacity(pg_session: AsyncSession) -> None:
    """Година простою при 2 rps не дає видати більше за `capacity_tokens` поспіль."""
    await _bucket(pg_session, ORIGIN_A, capacity=3, rps="2", concurrency=10)
    async with pg_session.begin():
        # Спалюємо стартовий burst.
        for _ in range(3):
            decision = await limiter.acquire_permit(pg_session, ORIGIN_A, "a", 1, now=T0)
            assert decision.granted
        assert (await limiter.acquire_permit(pg_session, ORIGIN_A, "a", 1, now=T0)).reason == "rate"
    long_pause = T0 + timedelta(hours=1)
    granted = 0
    async with pg_session.begin():
        for _ in range(10):
            decision = await limiter.acquire_permit(pg_session, ORIGIN_A, "a", 1, now=long_pause)
            if decision.granted:
                granted += 1
            else:
                assert decision.reason == "rate"
                break
    assert granted == 3, "burst після паузи має дорівнювати capacity_tokens, не часу простою"
    async with pg_session.begin():
        bucket = await limiter.get_bucket(pg_session, ORIGIN_A)
    assert bucket is not None
    assert bucket.available_tokens == Decimal("0")


async def test_two_origins_do_not_block_each_other(pg_session: AsyncSession) -> None:
    await _bucket(pg_session, ORIGIN_A, capacity=1, rps="1", concurrency=1)
    await _bucket(pg_session, ORIGIN_B, capacity=1, rps="1", concurrency=1)
    async with pg_session.begin():
        a1 = await limiter.acquire_permit(pg_session, ORIGIN_A, "w", 60, now=T0)
        b1 = await limiter.acquire_permit(pg_session, ORIGIN_B, "w", 60, now=T0)
        assert a1.granted and b1.granted
        assert (
            await limiter.acquire_permit(pg_session, ORIGIN_A, "w", 60, now=T0)
        ).granted is False
    async with pg_session.begin():
        await limiter.block_origin(
            pg_session, ORIGIN_A, T0 + timedelta(hours=1), reason="429", now=T0
        )
    async with pg_session.begin():
        assert (
            await limiter.acquire_permit(pg_session, ORIGIN_A, "w", 60, now=T0)
        ).reason == "blocked"
        later = T0 + timedelta(seconds=5)
        assert (await limiter.acquire_permit(pg_session, ORIGIN_B, "w", 60, now=later)).reason == (
            "concurrency"
        )
        assert b1.permit is not None
        assert await limiter.release_permit(pg_session, b1.permit.permit_id, now=later) is True
        assert (await limiter.acquire_permit(pg_session, ORIGIN_B, "w", 60, now=later)).granted
    async with pg_session.begin():
        bucket_b = await limiter.get_bucket(pg_session, ORIGIN_B)
    assert bucket_b is not None and bucket_b.blocked_until is None


async def test_expiry_frees_concurrency_slot_even_after_rate_token_spent(
    pg_session: AsyncSession,
) -> None:
    """capacity=1 (token витрачено при видачі) + concurrency=1: після lease expiry slot вільний,
    і як тільки token поповнився — видача проходить без `release_permit` (crash worker)."""
    await _bucket(pg_session, ORIGIN_A, capacity=1, rps="1", concurrency=1)
    async with pg_session.begin():
        first = await limiter.acquire_permit(pg_session, ORIGIN_A, "crashed", 5, now=T0)
    assert first.granted and first.permit is not None
    async with pg_session.begin():
        bucket = await limiter.get_bucket(pg_session, ORIGIN_A)
        assert bucket is not None and bucket.available_tokens == Decimal("0")
        blocked = await limiter.acquire_permit(pg_session, ORIGIN_A, "next", 5, now=T0)
    assert blocked.reason == "rate"  # rate вичерпано раніше за concurrency

    after = T0 + timedelta(seconds=6)
    async with pg_session.begin():
        # Permit ще не released (worker упав), але lease прострочений → slot вільний.
        stale = await pg_session.get(type(first.permit), first.permit.permit_id)
        assert stale is not None and stale.released_at is None
        granted = await limiter.acquire_permit(pg_session, ORIGIN_A, "next", 5, now=after)
    assert granted.granted and granted.live_permits == 1
    async with pg_session.begin():
        assert await limiter.expire_permits(pg_session, origin=ORIGIN_A, now=after) == 1
        assert await limiter.live_permit_count(pg_session, ORIGIN_A, now=after) == 1


# --------------------------------------------------------------------------- crawl runs


async def test_parallel_start_full_run_grants_exactly_one(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_sessions() as session, session.begin():
        source = await sources.create_source(
            session, source_id="news_ua_example", domain=DataDomain.NEWS, country="UA", now=T0
        )
        source_id = source.id

    async def starter(index: int) -> str:
        async with pg_sessions() as session, session.begin():
            try:
                await crawl_runs.start_run(
                    session, source_id, "full", started_by=f"scheduler-{index}", now=T0
                )
            except ConflictError:
                return "conflict"
            await asyncio.sleep(0.25)
            return "started"

    outcomes = await asyncio.gather(*(starter(i) for i in range(5)))
    assert outcomes.count("started") == 1, outcomes
    assert outcomes.count("conflict") == 4
    async with pg_sessions() as session:
        running = await session.scalar(
            select(func.count())
            .select_from(CrawlRun)
            .where(CrawlRun.status == "running", CrawlRun.kind == "full")
        )
    assert running == 1


# --------------------------------------------------------------------------- pools


async def _pool(session: AsyncSession, role: WorkerRole) -> None:
    async with session.begin():
        await pools.upsert_pool(
            session,
            role,
            pools.PoolDesiredState(desired_replicas=2, desired_concurrency=4, max_replicas=6),
            actor="bootstrap",
            reason="defaults",
            expected_revision=None,
            now=T0,
        )


async def test_scale_command_with_stale_revision_leaves_no_partial_writes(
    pg_session: AsyncSession,
) -> None:
    await _pool(pg_session, WorkerRole.FETCH)
    async with pg_session.begin():
        command = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=1,
            requested_replicas=4,
            requested_concurrency=4,
            actor="operator",
            reason="load",
            idempotency_key="scale-1",
            now=T0,
        )
    assert command.applied_pool_revision == 2

    async with pg_session.begin():
        with pytest.raises(StaleRevisionError):
            await pools.request_scale(
                pg_session,
                WorkerRole.FETCH,
                expected_revision=1,  # прострочена revision (GUI з іншого таба)
                requested_replicas=99,
                requested_concurrency=99,
                actor="operator",
                reason="stale",
                idempotency_key="scale-stale",
                now=T0,
            )
    async with pg_session.begin():
        pool = await pools.get_pool(pg_session, WorkerRole.FETCH)
        commands = list(await pg_session.scalars(select(ScaleCommand)))
        audits = list(await pg_session.scalars(select(AuditLog)))
    assert pool is not None
    assert (pool.revision, pool.desired_replicas) == (2, 4)
    assert [c.idempotency_key for c in commands] == ["scale-1"]
    assert [a.idempotency_key for a in audits] == ["scale-1"]


async def test_scale_command_with_foreign_pool_revision_is_rejected(
    pg_session: AsyncSession,
) -> None:
    """Revision іншого pool не підходить: pools версіонуються незалежно."""
    await _pool(pg_session, WorkerRole.FETCH)
    await _pool(pg_session, WorkerRole.PARSE)
    async with pg_session.begin():
        await pools.request_scale(
            pg_session,
            WorkerRole.PARSE,
            expected_revision=1,
            requested_replicas=3,
            requested_concurrency=2,
            actor="operator",
            reason="parse load",
            idempotency_key="parse-1",
            now=T0,
        )  # PARSE → revision 2, FETCH лишається 1
    async with pg_session.begin():
        with pytest.raises(StaleRevisionError):
            await pools.request_scale(
                pg_session,
                WorkerRole.FETCH,
                expected_revision=2,  # revision із чужого pool
                requested_replicas=5,
                requested_concurrency=5,
                actor="operator",
                reason="wrong pool",
                idempotency_key="fetch-wrong",
                now=T0,
            )
    async with pg_session.begin():
        fetch = await pools.get_pool(pg_session, WorkerRole.FETCH)
        keys = sorted(c.idempotency_key for c in await pg_session.scalars(select(ScaleCommand)))
    assert fetch is not None and fetch.revision == 1
    assert keys == ["parse-1"]


async def test_scale_command_terminal_and_skipping_transitions_are_rejected(
    pg_session: AsyncSession,
) -> None:
    await _pool(pg_session, WorkerRole.FETCH)
    async with pg_session.begin():
        command = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=1,
            requested_replicas=0,
            requested_concurrency=4,
            actor="operator",
            reason="drain",
            idempotency_key="scale-term",
            now=T0,
        )
    # requested → applied напряму заборонено (пропущено draining).
    async with pg_session.begin():
        with pytest.raises(InvalidTransitionError):
            await pools.transition_scale_command(pg_session, command.command_id, "applied", now=T0)
    async with pg_session.begin():
        with pytest.raises(InvalidTransitionError):
            await pools.transition_scale_command(
                pg_session, command.command_id, "requested", now=T0
            )
    async with pg_session.begin():
        with pytest.raises(InvalidTransitionError):
            await pools.transition_scale_command(pg_session, command.command_id, "unknown", now=T0)
    async with pg_session.begin():
        await pools.transition_scale_command(pg_session, command.command_id, "failed", now=T0)
    for target in ("draining", "applying", "applied", "superseded"):
        async with pg_session.begin():
            with pytest.raises(InvalidTransitionError):
                await pools.transition_scale_command(pg_session, command.command_id, target, now=T0)
    async with pg_session.begin():
        with pytest.raises(NotFoundError):
            await pools.transition_scale_command(pg_session, new_entity_id(), "draining", now=T0)
    async with pg_session.begin():
        final = await pools.get_scale_command(pg_session, command.command_id)
    assert final is not None and final.status == "failed"


async def test_heartbeat_from_stopped_instance_is_rejected_and_state_unchanged(
    pg_session: AsyncSession,
) -> None:
    await _pool(pg_session, WorkerRole.FETCH)
    instance_id = new_entity_id()
    async with pg_session.begin():
        await pools.register_instance(
            pg_session, instance_id, WorkerRole.FETCH, version="v1", slots_total=4, now=T0
        )
        await pools.mark_ready(pg_session, instance_id, now=T0)
        await pools.mark_draining(pg_session, instance_id, now=T0)
        stopped = await pools.mark_stopped(pg_session, instance_id, now=T0)
    assert stopped.status == "stopped"
    late = T0 + timedelta(minutes=1)
    async with pg_session.begin():
        with pytest.raises(InvalidTransitionError):
            await pools.heartbeat_instance(
                pg_session, instance_id, slots_active=4, active_leases=4, pool_revision=1, now=late
            )
    async with pg_session.begin():
        with pytest.raises(InvalidTransitionError):
            await pools.mark_ready(pg_session, instance_id, now=late)
        # `stopped` не потрапляє у stale-sweep і не додає capacity.
        assert await pools.mark_stale_instances(pg_session, now=late) == []
        observed = await pools.observed_capacity(pg_session, WorkerRole.FETCH, now=late)
    assert observed == pools.ObservedCapacity(0, 0, None)
    async with pg_session.begin():
        instance = await pg_session.get(type(stopped), instance_id, populate_existing=True)
    assert instance is not None
    assert (instance.status, instance.last_heartbeat_at, instance.slots_active) == (
        "stopped",
        T0,
        0,
    )


# --------------------------------------------------------------------------- migrations


async def test_upgrade_head_twice_in_a_row_is_idempotent(
    pg_empty_database: PostgresSettings,
) -> None:
    engine = create_async_engine(pg_empty_database.url, poolclass=None)
    try:
        async with engine.begin() as conn:
            await upgrade_to_head(conn)
        async with engine.begin() as conn:
            await upgrade_to_head(conn)
        async with engine.connect() as conn:
            assert await current_revision(conn) == "0001_control_queue"
            assert await check_no_drift(conn) == []
    finally:
        await engine.dispose()
