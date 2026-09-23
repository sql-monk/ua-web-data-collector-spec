"""Upload claim §10 п.5 (R-38/R-41): fencing `claim_generation`, lease, orphan candidates.

Картка PR2: stale generation не може commit; expired lease → reacquire іншим owner → старий
commit падає; concurrent 2 producers одного key → рівно один reference. Час — fake clock.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.errors import StaleClaimError
from collector.persistence.postgres.models import ArtifactUploadClaim, RawObject
from collector.persistence.postgres.repositories import artifacts

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
KEY = "raw/ab/" + "ab" * 32
SHA = "ab" * 32
URI = f"s3://raw/{KEY}"


async def _acquire(
    session: AsyncSession, owner: str, *, now: datetime, key: str = KEY
) -> ArtifactUploadClaim:
    async with session.begin():
        return await artifacts.acquire_upload_claim(session, key, owner, lease_seconds=30, now=now)


async def _commit(
    session: AsyncSession, owner: str, generation: int, *, now: datetime, sha: str = SHA
) -> ArtifactUploadClaim:
    async with session.begin():
        return await artifacts.commit_reference(
            session, KEY, generation, owner=owner, sha256=sha, size_bytes=10, uri=URI, now=now
        )


async def test_first_acquire_has_generation_one_and_commit_creates_reference(
    pg_session: AsyncSession,
) -> None:
    claim = await _acquire(pg_session, "fetch-1", now=T0)
    assert (claim.claim_generation, claim.status, claim.owner) == (1, "leased", "fetch-1")
    committed = await _commit(pg_session, "fetch-1", 1, now=T0 + timedelta(seconds=5))
    assert committed.status == "committed"
    assert (committed.sha256, committed.size_bytes, committed.uri) == (SHA, 10, URI)
    # Повтор того самого commit (retry після таймауту відповіді) ідемпотентний.
    again = await _commit(pg_session, "fetch-1", 1, now=T0 + timedelta(seconds=6))
    assert again.claim_id == committed.claim_id
    # Інші bytes за тим самим ключем — уже інший об'єкт: відмова.
    with pytest.raises(StaleClaimError):
        await _commit(pg_session, "fetch-1", 1, now=T0 + timedelta(seconds=6), sha="cd" * 32)


async def test_stale_generation_cannot_commit(pg_session: AsyncSession) -> None:
    first_generation = (await _acquire(pg_session, "fetch-1", now=T0)).claim_generation
    # Lease сплив → reacquire іншим owner атомарно піднімає generation.
    second = await _acquire(pg_session, "fetch-2", now=T0 + timedelta(seconds=31))
    assert (first_generation, second.claim_generation) == (1, 2)
    assert second.owner == "fetch-2"
    # Старий producer (після паузи GC/мережі) приходить зі своїм generation=1.
    with pytest.raises(StaleClaimError, match="generation"):
        await _commit(pg_session, "fetch-1", 1, now=T0 + timedelta(seconds=32))
    # Навіть з правильним generation, але чужим owner — відмова.
    with pytest.raises(StaleClaimError):
        await _commit(pg_session, "fetch-1", 2, now=T0 + timedelta(seconds=32))
    committed = await _commit(pg_session, "fetch-2", 2, now=T0 + timedelta(seconds=33))
    assert (committed.claim_generation, committed.owner) == (2, "fetch-2")


async def test_expired_lease_blocks_commit_even_for_the_owner(pg_session: AsyncSession) -> None:
    """Власник, чий lease сплив, мусить reacquire і повторити HEAD — commit не проходить."""
    await _acquire(pg_session, "fetch-1", now=T0)
    with pytest.raises(StaleClaimError):
        await _commit(pg_session, "fetch-1", 1, now=T0 + timedelta(seconds=30))
    renewed = await _acquire(pg_session, "fetch-1", now=T0 + timedelta(seconds=31))
    assert renewed.claim_generation == 2
    assert (await _commit(pg_session, "fetch-1", 2, now=T0 + timedelta(seconds=32))).status == (
        "committed"
    )


async def test_live_lease_of_another_owner_is_not_taken_and_own_renewal_keeps_generation(
    pg_session: AsyncSession,
) -> None:
    await _acquire(pg_session, "fetch-1", now=T0)
    with pytest.raises(StaleClaimError, match="живий lease"):
        await _acquire(pg_session, "fetch-2", now=T0 + timedelta(seconds=10))
    renewed = await _acquire(pg_session, "fetch-1", now=T0 + timedelta(seconds=20))
    assert renewed.claim_generation == 1
    assert renewed.lease_expires_at == T0 + timedelta(seconds=50)


async def test_committed_key_is_never_reacquired(pg_session: AsyncSession) -> None:
    await _acquire(pg_session, "fetch-1", now=T0)
    await _commit(pg_session, "fetch-1", 1, now=T0 + timedelta(seconds=1))
    with pytest.raises(StaleClaimError, match="committed"):
        await _acquire(pg_session, "fetch-2", now=T0 + timedelta(hours=1))


async def test_two_concurrent_producers_of_one_key_create_exactly_one_reference(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Обидва producers проходять повний цикл acquire → PUT → commit паралельно."""
    barrier = asyncio.Barrier(2)

    async def producer(owner: str) -> str:
        async with pg_sessions() as session:
            await barrier.wait()
            try:
                claim = await _acquire(session, owner, now=T0)
            except StaleClaimError:
                return "lost-acquire"
            await asyncio.sleep(0)  # «PUT + HEAD» поза транзакцією
            try:
                await _commit(session, owner, claim.claim_generation, now=T0 + timedelta(seconds=1))
            except StaleClaimError:
                return "lost-commit"
            return "committed"

    outcomes = sorted(await asyncio.gather(producer("p-1"), producer("p-2")))
    assert outcomes == ["committed", "lost-acquire"]
    async with pg_sessions() as session, session.begin():
        rows = list(await session.scalars(select(ArtifactUploadClaim)))
    assert len(rows) == 1 and rows[0].status == "committed"


async def test_orphan_candidates_exclude_references_and_live_claims(
    pg_session: AsyncSession,
) -> None:
    grace = timedelta(minutes=10)
    later = T0 + timedelta(hours=1)
    # 1) committed claim — DB reference, ніколи не кандидат;
    await _acquire(pg_session, "w", now=T0)
    await _commit(pg_session, "w", 1, now=T0 + timedelta(seconds=1))
    # 2) прострочений claim без reference — кандидат;
    await _acquire(pg_session, "w", now=T0, key="raw/orphan")
    # 3) прострочений claim, але на ключ посилається raw_objects — не кандидат;
    await _acquire(pg_session, "w", now=T0, key="raw/referenced")
    async with pg_session.begin():
        await artifacts.record_raw_object(
            pg_session,
            sha256="ef" * 32,
            object_key="raw/referenced",
            uri="s3://raw/raw/referenced",
            size_bytes=1,
            media_type="text/html",
            now=T0,
        )
    # 4) живий claim — не кандидат.
    await _acquire(pg_session, "w", now=later - timedelta(seconds=5), key="raw/live")
    async with pg_session.begin():
        candidates = await artifacts.list_orphan_candidates(pg_session, grace=grace, now=later)
        # Lease сплив менше ніж grace тому → ще не кандидат (sweeper не змагається з HEAD).
        too_early = await artifacts.list_orphan_candidates(
            pg_session, grace=grace, now=T0 + timedelta(seconds=31)
        )
    assert candidates == ["raw/orphan"]
    assert too_early == []


async def test_record_raw_object_deduplicates_by_content_hash(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        first = await artifacts.record_raw_object(
            pg_session,
            sha256=SHA,
            object_key=KEY,
            uri=URI,
            size_bytes=10,
            media_type="text/html",
            now=T0,
        )
        again = await artifacts.record_raw_object(
            pg_session,
            sha256=SHA,
            object_key=KEY,
            uri=URI,
            size_bytes=10,
            media_type="text/html",
            now=T0 + timedelta(days=1),
        )
    assert again.raw_object_id == first.raw_object_id
    assert again.first_seen_at == T0
    async with pg_session.begin():
        assert await pg_session.scalar(select(func.count()).select_from(RawObject)) == 1


async def test_release_and_expire_claims(pg_session: AsyncSession) -> None:
    await _acquire(pg_session, "w", now=T0, key="raw/released")
    await _acquire(pg_session, "w", now=T0, key="raw/expiring")
    async with pg_session.begin():
        released = await artifacts.release_claim(pg_session, "raw/released", 1, owner="w", now=T0)
        assert released is not None and released.status == "released"
        # Чужий owner нічого не звільняє.
        assert await artifacts.release_claim(pg_session, "raw/expiring", 1, owner="x") is None
        expired = await artifacts.expire_claims(pg_session, now=T0 + timedelta(minutes=1))
    assert expired == ["raw/expiring"]


async def test_sweeper_fencing_protocol_blocks_the_race_with_a_new_producer(
    pg_session: AsyncSession,
) -> None:
    """Gate 3, CR-4: sweeper видаляє orphan лише під власним claim.

    1) кандидат є; 2) sweeper бере claim — producer, що прийшов саме тоді, отримує
    `StaleClaimError` і не пише об'єкт, а старий producer не комітить свою generation;
    3) sweeper видаляє об'єкт і звільняє claim; 4) новий producer перебирає ключ із новою
    generation, пише заново і комітить. Якщо ж producer встиг перебрати ключ **до** sweeper-а,
    claim sweeper-а відхиляється — видалення не відбувається.
    """
    grace = timedelta(minutes=10)
    later = T0 + timedelta(hours=1)
    stale = await _acquire(pg_session, "fetch-old", now=T0)  # lease сплив, reference немає
    stale_generation = stale.claim_generation
    async with pg_session.begin():
        assert await artifacts.list_orphan_candidates(pg_session, grace=grace, now=later) == [KEY]
    sweeper = await _acquire(pg_session, "sweeper", now=later)
    sweeper_generation = sweeper.claim_generation
    with pytest.raises(StaleClaimError, match="живий lease"):
        await _acquire(pg_session, "fetch-new", now=later + timedelta(seconds=1))
    with pytest.raises(StaleClaimError):
        await _commit(pg_session, "fetch-old", stale_generation, now=later + timedelta(seconds=1))
    # … sweeper видаляє об'єкт з artifact store, поки його lease живий …
    async with pg_session.begin():
        released = await artifacts.release_claim(
            pg_session, KEY, sweeper_generation, owner="sweeper", now=later + timedelta(seconds=2)
        )
    assert released is not None and released.status == "released"
    fresh = await _acquire(pg_session, "fetch-new", now=later + timedelta(seconds=3))
    assert fresh.claim_generation == sweeper_generation + 1
    committed = await _commit(
        pg_session, "fetch-new", fresh.claim_generation, now=later + timedelta(seconds=4)
    )
    assert committed.status == "committed"
    # Тепер ключ має reference: sweeper його не отримає ні як кандидата, ні як claim.
    async with pg_session.begin():
        assert (
            await artifacts.list_orphan_candidates(
                pg_session, grace=grace, now=later + timedelta(days=1)
            )
            == []
        )
    with pytest.raises(StaleClaimError, match="committed"):
        await _acquire(pg_session, "sweeper", now=later + timedelta(days=1))


async def test_sweeper_claim_is_refused_when_a_producer_reacquired_first(
    pg_session: AsyncSession,
) -> None:
    grace = timedelta(minutes=10)
    later = T0 + timedelta(hours=1)
    await _acquire(pg_session, "fetch-old", now=T0)
    async with pg_session.begin():
        candidates = await artifacts.list_orphan_candidates(pg_session, grace=grace, now=later)
    assert candidates == [KEY]
    # Між SELECT sweeper-а і його claim producer перебрав ключ.
    await _acquire(pg_session, "fetch-new", now=later)
    with pytest.raises(StaleClaimError, match="живий lease"):
        await _acquire(pg_session, "sweeper", now=later + timedelta(seconds=1))
