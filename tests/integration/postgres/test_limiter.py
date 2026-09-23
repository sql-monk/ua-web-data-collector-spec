"""Origin limiter §7.6 / R-53: 8 паралельних acquirers → рівно 1 slot; rate 0.2 rps за 10 с
(simulated clock) ≤ 3; expired permit відновлює slot; block_origin; release ідемпотентний."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.errors import NotFoundError
from collector.persistence.postgres.repositories import limiter

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
ORIGIN = "https://example.test"


async def _bucket(
    sessions: async_sessionmaker[AsyncSession],
    *,
    capacity: int,
    rps: str,
    concurrency: int,
) -> None:
    async with sessions() as session, session.begin():
        await limiter.ensure_bucket(
            session,
            ORIGIN,
            capacity_tokens=capacity,
            refill_per_second=rps,
            max_concurrency=concurrency,
            now=T0,
        )


async def test_eight_parallel_acquirers_get_exactly_one_slot_with_concurrency_one(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Rate не обмежує (100 tokens), обмежує лише concurrency=1.
    await _bucket(pg_sessions, capacity=100, rps="1000", concurrency=1)

    async def acquirer(name: str) -> limiter.PermitDecision:
        async with pg_sessions() as session, session.begin():
            return await limiter.acquire_permit(session, ORIGIN, name, 30, now=T0)

    decisions = await asyncio.gather(*(acquirer(f"replica-{i}") for i in range(8)))
    granted = [d for d in decisions if d.granted]
    denied = [d for d in decisions if not d.granted]
    assert len(granted) == 1
    assert len(denied) == 7
    assert {d.reason for d in denied} == {"concurrency"}
    assert all(d.retry_after == T0 + timedelta(seconds=30) for d in denied)
    async with pg_sessions() as session:
        assert await limiter.live_permit_count(session, ORIGIN, now=T0) == 1
        bucket = await limiter.get_bucket(session, ORIGIN)
        assert bucket is not None
        assert bucket.available_tokens == Decimal("99")  # лише виданий permit списав token


async def test_eight_parallel_acquirers_with_concurrency_three(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _bucket(pg_sessions, capacity=100, rps="1000", concurrency=3)

    async def acquirer(name: str) -> limiter.PermitDecision:
        async with pg_sessions() as session, session.begin():
            return await limiter.acquire_permit(session, ORIGIN, name, 30, now=T0)

    decisions = await asyncio.gather(*(acquirer(f"replica-{i}") for i in range(8)))
    assert sum(d.granted for d in decisions) == 3


async def test_rate_limit_0_2_rps_grants_at_most_three_in_ten_seconds(
    pg_session: AsyncSession,
) -> None:
    """capacity 1 + 0.2 rps: t=0 → 1 token, t=5 → +1, t=10 → +1 ⇒ рівно 3 видачі ≤ 3."""
    await _bucket_session(pg_session, capacity=1, rps="0.2", concurrency=1)
    grants = 0
    for tenths in range(0, 101):  # 0.0 … 10.0 с кроком 0.1 с
        now = T0 + timedelta(milliseconds=100 * tenths)
        async with pg_session.begin():
            decision = await limiter.acquire_permit(pg_session, ORIGIN, "r", 30, now=now)
            if decision.granted:
                grants += 1
                assert decision.permit is not None
                assert await limiter.release_permit(pg_session, decision.permit.permit_id, now=now)
            else:
                assert decision.reason == "rate"
                assert decision.retry_after is not None and decision.retry_after > now
    assert grants == 3
    # Ще через 5 с — четвертий token; tokens не накопичуються понад capacity=1.
    later = T0 + timedelta(seconds=100)
    async with pg_session.begin():
        first = await limiter.acquire_permit(pg_session, ORIGIN, "r", 30, now=later)
        assert first.granted and first.permit is not None
        await limiter.release_permit(pg_session, first.permit.permit_id, now=later)
        second = await limiter.acquire_permit(pg_session, ORIGIN, "r", 30, now=later)
    assert not second.granted and second.reason == "rate"


async def test_expired_permit_restores_slot_and_release_is_idempotent(
    pg_session: AsyncSession,
) -> None:
    await _bucket_session(pg_session, capacity=10, rps="10", concurrency=1)
    async with pg_session.begin():
        first = await limiter.acquire_permit(pg_session, ORIGIN, "a", 5, now=T0)
        assert first.granted and first.permit is not None
        blocked = await limiter.acquire_permit(pg_session, ORIGIN, "b", 5, now=T0)
        assert blocked.reason == "concurrency"
    after = T0 + timedelta(seconds=6)
    async with pg_session.begin():
        # Прострочений permit не рахується live навіть до expire_permits.
        second = await limiter.acquire_permit(pg_session, ORIGIN, "b", 5, now=after)
        assert second.granted
        assert second.live_permits == 1
    async with pg_session.begin():
        assert await limiter.expire_permits(pg_session, now=after) == 1  # first → expired
        assert await limiter.expire_permits(pg_session, now=after) == 0
        assert await limiter.release_permit(pg_session, first.permit.permit_id, now=after) is False
        assert second.permit is not None
        assert await limiter.release_permit(pg_session, second.permit.permit_id, now=after) is True
        assert await limiter.release_permit(pg_session, second.permit.permit_id, now=after) is False
        assert await limiter.live_permit_count(pg_session, ORIGIN, now=after) == 0


async def test_block_origin_denies_until_deadline(pg_session: AsyncSession) -> None:
    await _bucket_session(pg_session, capacity=10, rps="10", concurrency=4)
    until = T0 + timedelta(seconds=90)
    async with pg_session.begin():
        bucket = await limiter.block_origin(
            pg_session, ORIGIN, until, actor="fetcher", reason="429 Retry-After", now=T0
        )
        assert bucket.blocked_until == until
        shorter = await limiter.block_origin(
            pg_session, ORIGIN, T0 + timedelta(seconds=10), actor="fetcher", reason="dup", now=T0
        )
        assert shorter.blocked_until == until  # не скорочує
    async with pg_session.begin():
        denied = await limiter.acquire_permit(
            pg_session, ORIGIN, "a", 5, now=until - timedelta(seconds=1)
        )
        assert denied.reason == "blocked" and denied.retry_after == until
        # Рівно в `until` блок знято, але токени починаються з нуля (M-4): перша видача — лише
        # після звичайного refill, а не одразу і не повним burst-ом.
        at_deadline = await limiter.acquire_permit(pg_session, ORIGIN, "a", 5, now=until)
        assert not at_deadline.granted and at_deadline.reason == "rate"
        granted = await limiter.acquire_permit(
            pg_session, ORIGIN, "a", 5, now=until + timedelta(seconds=1)
        )
        assert granted.granted


async def test_unknown_origin_is_not_guessed(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        with pytest.raises(NotFoundError):
            await limiter.acquire_permit(pg_session, "https://unknown.test", "a", 5, now=T0)


async def test_ensure_bucket_updates_config_without_overfilling(pg_session: AsyncSession) -> None:
    await _bucket_session(pg_session, capacity=10, rps="1", concurrency=2)
    async with pg_session.begin():
        bucket = await limiter.ensure_bucket(
            pg_session,
            ORIGIN,
            capacity_tokens=3,
            refill_per_second="0.5",
            max_concurrency=1,
            now=T0,
        )
    assert bucket.available_tokens == Decimal("3")
    assert bucket.max_concurrency == 1
    assert bucket.revision == 2


async def _bucket_session(
    session: AsyncSession, *, capacity: int, rps: str, concurrency: int
) -> None:
    async with session.begin():
        await limiter.ensure_bucket(
            session,
            ORIGIN,
            capacity_tokens=capacity,
            refill_per_second=rps,
            max_concurrency=concurrency,
            now=T0,
        )


async def test_block_origin_resets_refill_so_there_is_no_burst_after_unblock(
    pg_session: AsyncSession,
) -> None:
    """M-4: після зняття 429-блокування origin не отримує накопичений burst.

    Сценарій рев'ю: токени вичерпано → 429 → `block_origin(+1 год)` → через годину перші ж
    виклики видавали повний `capacity_tokens` поспіль, тобто сплеск саме до сайта, який щойно
    нас забанив.
    """
    await _bucket_session(pg_session, capacity=5, rps="0.2", concurrency=5)
    until = T0 + timedelta(hours=1)
    async with pg_session.begin():
        # Вичерпати токени (capacity=5) і отримати 429.
        for _ in range(5):
            decision = await limiter.acquire_permit(pg_session, ORIGIN, "w", 1, now=T0)
            assert decision.granted
            assert decision.permit is not None
            await limiter.release_permit(pg_session, decision.permit.permit_id, now=T0)
        bucket = await limiter.block_origin(
            pg_session, ORIGIN, until, actor="fetcher", reason="429", now=T0
        )
    assert bucket.available_tokens == Decimal(0)
    assert bucket.last_refill_at == until

    after = until + timedelta(seconds=1)
    async with pg_session.begin():
        first = await limiter.acquire_permit(pg_session, ORIGIN, "w", 1, now=after)
        assert not first.granted
        assert first.reason == "rate"  # блок знято, але токенів ще немає
        # Один токен зʼявляється рівно через 1/0.2 = 5 с після зняття блоку, не одразу.
        five_seconds_later = until + timedelta(seconds=5)
        second = await limiter.acquire_permit(pg_session, ORIGIN, "w", 1, now=five_seconds_later)
        assert second.granted
        third = await limiter.acquire_permit(pg_session, ORIGIN, "w", 1, now=five_seconds_later)
        assert not third.granted and third.reason == "rate"


async def test_release_permit_with_owner_does_not_free_foreign_slot(
    pg_session: AsyncSession,
) -> None:
    """L-5: помилка у власному стані викликача не повинна звільняти чужий concurrency slot."""
    await _bucket_session(pg_session, capacity=10, rps="10", concurrency=2)
    async with pg_session.begin():
        mine = await limiter.acquire_permit(pg_session, ORIGIN, "worker-a", 60, now=T0)
        theirs = await limiter.acquire_permit(pg_session, ORIGIN, "worker-b", 60, now=T0)
        assert mine.permit is not None and theirs.permit is not None

        # worker-a намагається звільнити permit worker-b (переплутаний id).
        assert (
            await limiter.release_permit(
                pg_session, theirs.permit.permit_id, owner_instance="worker-a", now=T0
            )
            is False
        )
        assert await limiter.live_permit_count(pg_session, ORIGIN, now=T0) == 2

        assert (
            await limiter.release_permit(
                pg_session, theirs.permit.permit_id, owner_instance="worker-b", now=T0
            )
            is True
        )
        assert await limiter.live_permit_count(pg_session, ORIGIN, now=T0) == 1
        # Sweeper без знання власника працює як раніше.
        assert await limiter.release_permit(pg_session, mine.permit.permit_id, now=T0) is True
