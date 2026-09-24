"""Claimed/verified PUT orchestration (§10 п.5, R-33/R-38/R-41).

PostgreSQL transactions are intentionally short: acquire, S3 PUT+HEAD outside transaction,
then fenced claim commit and caller reference callback in one transaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts.enums import UploadClaimStatus
from collector.persistence.postgres.errors import StaleClaimError
from collector.persistence.postgres.models import ArtifactUploadClaim
from collector.persistence.postgres.repositories import artifacts
from collector.storage.store import ArtifactIntegrityError, ArtifactStore, StoredObject

ReferenceT = TypeVar("ReferenceT")
CommitReference = Callable[[AsyncSession, StoredObject], Awaitable[ReferenceT]]


class UploadBusyError(RuntimeError):
    """Інший producer тримає живий claim; handler має defer/retry, не робити parallel PUT."""


class UploadRetriesExhaustedError(RuntimeError):
    """Claim ставав stale на кожній bounded спробі."""


@dataclass(frozen=True, slots=True)
class ClaimedUploadResult[ReferenceT]:
    stored: StoredObject
    reference: ReferenceT
    deduplicated: bool
    attempts: int


class ClaimedUploader:
    """Generic claimed PUT для raw/normalized/translated/event/archive artifacts."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: ArtifactStore,
        *,
        owner: str,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not owner:
            raise ValueError("owner має бути непорожнім")
        if lease_seconds < 1:
            raise ValueError("lease_seconds має бути >= 1")
        if max_attempts < 1:
            raise ValueError("max_attempts має бути >= 1")
        self._sessions = sessions
        self._store = store
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._clock = clock

    async def upload(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        sha256: str,
        media_type: str,
        commit: CommitReference[ReferenceT],
        after_put: Callable[[], Awaitable[None]] | None = None,
    ) -> ClaimedUploadResult[ReferenceT]:
        """Upload bytes and atomically commit claim + caller DB reference.

        `after_put` is a fault-injection seam used by integration tests. If it raises, the
        leased claim and object remain; a later owner can reacquire after expiry and verify PUT.
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                claim = await self._acquire(key, media_type)
            except StaleClaimError:
                existing = await self._committed_claim(key)
                if existing is None:
                    raise UploadBusyError(f"upload claim {key!r} зайнятий іншим producer") from None
                stored = await self._verify_existing(bucket, key, sha256, len(data), media_type)
                async with self._sessions() as session, session.begin():
                    reference = await commit(session, stored)
                return ClaimedUploadResult(stored, reference, True, attempt)

            stored = await self._store.put(bucket, key, data, sha256=sha256, media_type=media_type)
            if after_put is not None:
                await after_put()
            try:
                async with self._sessions() as session, session.begin():
                    await artifacts.commit_reference(
                        session,
                        key,
                        claim.claim_generation,
                        owner=self._owner,
                        sha256=stored.sha256,
                        size_bytes=stored.size,
                        uri=stored.uri,
                        media_type=stored.media_type,
                        now=self._clock(),
                    )
                    reference = await commit(session, stored)
                return ClaimedUploadResult(stored, reference, False, attempt)
            except StaleClaimError:
                # Lease expired/reacquired between HEAD and commit: loop must reacquire and
                # repeat PUT+HEAD; reusing this HEAD would bypass generation fencing.
                continue
        raise UploadRetriesExhaustedError(
            f"upload claim {key!r} став stale {self._max_attempts} разів"
        )

    async def _acquire(self, key: str, media_type: str) -> ArtifactUploadClaim:
        async with self._sessions() as session, session.begin():
            return await artifacts.acquire_upload_claim(
                session,
                key,
                self._owner,
                lease_seconds=self._lease_seconds,
                media_type=media_type,
                now=self._clock(),
            )

    async def _committed_claim(self, key: str) -> ArtifactUploadClaim | None:
        async with self._sessions() as session:
            claim = await artifacts.get_claim(session, key)
            if claim is None or claim.status != UploadClaimStatus.COMMITTED.value:
                return None
            return claim

    async def _verify_existing(
        self, bucket: str, key: str, sha256: str, size: int, media_type: str
    ) -> StoredObject:
        head = await self._store.head(bucket, key)
        if head.sha256 != sha256 or head.size != size:
            raise ArtifactIntegrityError(
                f"committed s3://{bucket}/{key} не збігається з content-addressed input"
            )
        return StoredObject(
            bucket=bucket,
            key=key,
            size=head.size,
            sha256=head.sha256,
            etag=head.etag,
            media_type=head.media_type or media_type,
        )


__all__ = [
    "ClaimedUploadResult",
    "ClaimedUploader",
    "CommitReference",
    "UploadBusyError",
    "UploadRetriesExhaustedError",
]
