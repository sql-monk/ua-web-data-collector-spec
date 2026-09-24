"""Claimed/verified PUT orchestration against real PostgreSQL claim rows."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.models import ArtifactUploadClaim, RawObject
from collector.persistence.postgres.repositories import artifacts
from collector.storage import (
    ArtifactNotFoundError,
    ClaimedUploader,
    StoredObject,
    UploadBusyError,
    sweep_orphans,
)
from collector.storage.testing import FakeArtifactStore

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
BODY = b"immutable raw response"
SHA = hashlib.sha256(BODY).hexdigest()
KEY = f"raw/{SHA[:2]}/{SHA}"


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def _raw_reference(session: AsyncSession, stored: StoredObject) -> RawObject:
    return await artifacts.record_raw_object(
        session,
        sha256=stored.sha256,
        object_key=stored.key,
        uri=stored.uri,
        size_bytes=stored.size,
        media_type=stored.media_type or "application/octet-stream",
        now=T0,
    )


async def _counts(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with sessions() as session:
        claims = int(
            await session.scalar(select(func.count()).select_from(ArtifactUploadClaim)) or 0
        )
        raw = int(await session.scalar(select(func.count()).select_from(RawObject)) or 0)
    return claims, raw


async def test_success_and_replay_after_commit_deduplicate(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = FakeArtifactStore(buckets={"raw"})
    first = await ClaimedUploader(pg_sessions, store, owner="worker-1", clock=Clock()).upload(
        bucket="raw",
        key=KEY,
        data=BODY,
        sha256=SHA,
        media_type="text/html",
        commit=_raw_reference,
    )
    replay = await ClaimedUploader(pg_sessions, store, owner="worker-2", clock=Clock()).upload(
        bucket="raw",
        key=KEY,
        data=BODY,
        sha256=SHA,
        media_type="text/html",
        commit=_raw_reference,
    )
    assert not first.deduplicated
    assert replay.deduplicated
    assert first.reference.raw_object_id == replay.reference.raw_object_id
    assert store.put_calls == [("raw", KEY)]
    assert await _counts(pg_sessions) == (1, 1)


async def test_crash_after_put_leaves_no_reference_and_next_owner_recovers(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = FakeArtifactStore(buckets={"raw"})
    clock = Clock()

    async def crash() -> None:
        raise RuntimeError("fault after PUT")

    with pytest.raises(RuntimeError, match="fault after PUT"):
        await ClaimedUploader(
            pg_sessions, store, owner="worker-1", lease_seconds=10, clock=clock
        ).upload(
            bucket="raw",
            key=KEY,
            data=BODY,
            sha256=SHA,
            media_type="text/html",
            commit=_raw_reference,
            after_put=crash,
        )
    assert await _counts(pg_sessions) == (1, 0)
    assert await store.head("raw", KEY)

    clock.now += timedelta(seconds=11)
    recovered = await ClaimedUploader(
        pg_sessions, store, owner="worker-2", lease_seconds=10, clock=clock
    ).upload(
        bucket="raw",
        key=KEY,
        data=BODY,
        sha256=SHA,
        media_type="text/html",
        commit=_raw_reference,
    )
    assert recovered.attempts == 1
    assert store.put_calls == [("raw", KEY), ("raw", KEY)]
    assert await _counts(pg_sessions) == (1, 1)


async def test_lease_expiry_between_head_and_commit_repeats_put_and_head(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = FakeArtifactStore(buckets={"raw"})
    clock = Clock()
    calls = 0

    async def expire_first_generation() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            clock.now += timedelta(seconds=11)

    result = await ClaimedUploader(
        pg_sessions,
        store,
        owner="worker-1",
        lease_seconds=10,
        max_attempts=2,
        clock=clock,
    ).upload(
        bucket="raw",
        key=KEY,
        data=BODY,
        sha256=SHA,
        media_type="text/html",
        commit=_raw_reference,
        after_put=expire_first_generation,
    )
    assert result.attempts == 2
    assert store.put_calls == [("raw", KEY), ("raw", KEY)]
    # Fake put itself performs no explicit head; the replay proof is two full verified PUTs.
    assert await _counts(pg_sessions) == (1, 1)


async def test_live_claim_of_other_producer_does_not_parallel_put(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_sessions() as session, session.begin():
        await artifacts.acquire_upload_claim(session, KEY, "worker-1", lease_seconds=30, now=T0)
    store = FakeArtifactStore(buckets={"raw"})
    with pytest.raises(UploadBusyError, match="зайнятий"):
        await ClaimedUploader(pg_sessions, store, owner="worker-2", clock=Clock()).upload(
            bucket="raw",
            key=KEY,
            data=BODY,
            sha256=SHA,
            media_type="text/html",
            commit=_raw_reference,
        )
    assert store.put_calls == []


async def test_sweeper_reacquires_before_delete_and_releases_generation(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = FakeArtifactStore(buckets={"raw"})
    await store.put("raw", KEY, BODY, sha256=SHA, media_type="text/html")
    async with pg_sessions() as session, session.begin():
        await artifacts.acquire_upload_claim(
            session, KEY, "dead-producer", lease_seconds=10, now=T0
        )
    clock = Clock(T0 + timedelta(minutes=5))
    report = await sweep_orphans(
        pg_sessions,
        store,
        grace=timedelta(minutes=2),
        minimum_grace=timedelta(minutes=1),
        owner="sweeper",
        bucket_for_key=lambda _key: "raw",
        lease_seconds=30,
        clock=clock,
    )
    assert report.deleted == (KEY,)
    assert store.delete_calls == [("raw", KEY)]
    with pytest.raises(ArtifactNotFoundError, match="не існує"):
        await store.head("raw", KEY)
    async with pg_sessions() as session:
        claim = await artifacts.get_claim(session, KEY)
    assert claim is not None
    assert (claim.owner, claim.claim_generation, claim.status) == ("sweeper", 2, "released")


async def test_sweeper_rejects_unsafe_grace(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ValueError, match="grace"):
        await sweep_orphans(
            pg_sessions,
            FakeArtifactStore(),
            grace=timedelta(minutes=1),
            minimum_grace=timedelta(minutes=1),
            owner="sweeper",
            bucket_for_key=lambda _key: "raw",
        )
