"""Origin permits (R-53, O-1): жоден байт до origin без виданого permit глобального limiter-а.

`OriginPermits` — тонкий Protocol, від якого залежить `SafeFetcher`. `PgOriginPermits` —
**тимчасовий** адаптер над `collector.persistence.postgres.repositories.limiter` (WP-01A):
кожна операція — окрема коротка транзакція (row lock bucket-а не тримається під час HTTP).
Прибрати `PgOriginPermits` після WP-01D PR2 і перейти на `OriginPermitClient`
(`collector.core.limiter_runtime`) — це стереже `tests/unit/fetch/test_permits_adapter_tripwire.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.errors import NotFoundError
from collector.persistence.postgres.repositories import limiter

DenyReason = Literal["blocked", "rate", "concurrency", "unknown_origin"]
DEFAULT_LEASE_SECONDS = 90  # > total timeout 60 с (§10) + запас на release


@dataclass(frozen=True, slots=True)
class Permit:
    permit_id: UUID
    origin: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class Denied:
    """Відмова limiter-а: запит не виконується. `unknown_origin` — bucket не створено."""

    reason: DenyReason
    retry_after: datetime | None = None


class OriginPermits(Protocol):
    async def acquire(self, origin: str, job_id: UUID | None) -> Permit | Denied: ...

    async def release(self, permit: Permit) -> None:
        """Ідемпотентно: повторний release того самого permit — no-op."""

    async def block(self, origin: str, until: datetime, reason: str) -> None: ...


class PgOriginPermits:
    """Тимчасовий адаптер (O-1) до PG-лімітера WP-01A; прибрати після WP-01D PR2."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        owner_instance: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self._sessions = sessions
        self._owner = owner_instance
        self._lease_seconds = lease_seconds

    async def acquire(self, origin: str, job_id: UUID | None) -> Permit | Denied:
        try:
            async with self._sessions() as session, session.begin():
                decision = await limiter.acquire_permit(
                    session, origin, self._owner, self._lease_seconds, job_id=job_id
                )
                if decision.granted and decision.permit is not None:
                    return Permit(
                        permit_id=decision.permit.permit_id,
                        origin=origin,
                        lease_expires_at=decision.permit.lease_expires_at,
                    )
        except NotFoundError:
            return Denied("unknown_origin")
        return Denied(decision.reason or "rate", decision.retry_after)

    async def release(self, permit: Permit) -> None:
        async with self._sessions() as session, session.begin():
            await limiter.release_permit(session, permit.permit_id, owner_instance=self._owner)

    async def block(self, origin: str, until: datetime, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            await limiter.block_origin(session, origin, until, actor=self._owner, reason=reason)
