"""Fenced orphan deletion: candidate select is never sufficient authority to delete."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.errors import StaleClaimError
from collector.persistence.postgres.repositories import artifacts
from collector.storage.store import ArtifactStore

if TYPE_CHECKING:
    from collector.fetch.metrics import FetchMetrics

BucketForKey = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class SweepReport:
    candidates: int
    deleted: tuple[str, ...]
    skipped: tuple[str, ...]


async def sweep_orphans(
    sessions: async_sessionmaker[AsyncSession],
    store: ArtifactStore,
    *,
    grace: timedelta,
    minimum_grace: timedelta,
    owner: str,
    bucket_for_key: BucketForKey,
    lease_seconds: int = 120,
    limit: int = 1000,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    metrics: FetchMetrics | None = None,
) -> SweepReport:
    """Delete only after sweeper reacquires each candidate with a fresh fencing generation.

    The producer that held the old generation cannot commit after this acquire. Release happens
    even when delete fails, so a future producer can PUT+HEAD again. `grace` must exceed the
    configured maximum PUT+HEAD+commit window (`minimum_grace`).
    """
    if grace <= minimum_grace:
        raise ValueError("grace має бути більшим за максимальне вікно PUT+HEAD+commit")
    async with sessions() as session:
        candidates = await artifacts.list_orphan_candidates(
            session, grace=grace, limit=limit, now=clock()
        )
    deleted: list[str] = []
    skipped: list[str] = []
    for key in candidates:
        try:
            async with sessions() as session, session.begin():
                claim = await artifacts.acquire_upload_claim(
                    session,
                    key,
                    owner,
                    lease_seconds=lease_seconds,
                    now=clock(),
                )
        except StaleClaimError:
            skipped.append(key)
            continue
        try:
            await store.delete(bucket_for_key(key), key)
            deleted.append(key)
        finally:
            async with sessions() as session, session.begin():
                await artifacts.release_claim(
                    session,
                    key,
                    claim.claim_generation,
                    owner=owner,
                    now=clock(),
                )
    if metrics is not None:
        metrics.observe_orphans(len(candidates))
    return SweepReport(len(candidates), tuple(deleted), tuple(skipped))


__all__ = ["BucketForKey", "SweepReport", "sweep_orphans"]
