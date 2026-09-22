"""Глобальний origin limiter §7.6 / FR-033 / R-53: canonical leased permits у PostgreSQL.

`acquire_permit` — одна коротка транзакція викликача з row lock на bucket
(`SELECT ... FOR UPDATE`): усі replicas discovery/fetch/browser серіалізуються на bucket,
тож видача детермінована незалежно від кількості контейнерів. Алгоритм:

1. bucket заблокований → відмова `blocked` з `retry_after = blocked_until`;
2. поповнення rate tokens за `refill_per_second` від `last_refill_at` до `now` (cap
   `capacity_tokens`), стан refill зберігається навіть при відмові;
3. live concurrency = permits з `released_at IS NULL AND lease_expires_at > now`;
4. permit видається лише якщо `tokens >= 1` **і** `live < max_concurrency`; rate token
   списується тільки при видачі, тож відмова через concurrency не «спалює» rate.

`release_permit` і `expire_permits` ідемпотентні (UPDATE з предикатом `released_at IS NULL`).
Прострочений permit не рахується live навіть до `expire_permits` — slot відновлюється сам.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import new_entity_id
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import NotFoundError
from collector.persistence.postgres.models import OriginRateBucket, OriginRatePermit

TOKEN_QUANTUM = Decimal("0.0001")
DenyReason = Literal["blocked", "rate", "concurrency"]


@dataclass(frozen=True, slots=True)
class PermitDecision:
    """Результат `acquire_permit`: або `permit`, або причина відмови з `retry_after`."""

    granted: bool
    permit: OriginRatePermit | None = None
    reason: DenyReason | None = None
    retry_after: datetime | None = None
    live_permits: int = 0
    available_tokens: Decimal = Decimal(0)


async def ensure_bucket(
    session: AsyncSession,
    origin: str,
    *,
    capacity_tokens: Decimal | int,
    refill_per_second: Decimal | float | str,
    max_concurrency: int,
    now: datetime | None = None,
) -> OriginRateBucket:
    """Створює bucket (повний) або оновлює конфігурацію з policy джерела (revision+1);
    `available_tokens` не перевищує нову capacity. Transaction boundary: викликач."""
    current = resolve_now(now)
    capacity = Decimal(capacity_tokens)
    refill = Decimal(str(refill_per_second))
    stmt = (
        pg_insert(OriginRateBucket)
        .values(
            origin=origin,
            capacity_tokens=capacity,
            refill_per_second=refill,
            available_tokens=capacity,
            last_refill_at=current,
            max_concurrency=max_concurrency,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_update(
            index_elements=[OriginRateBucket.origin],
            set_={
                "capacity_tokens": capacity,
                "refill_per_second": refill,
                "available_tokens": func.least(OriginRateBucket.available_tokens, capacity),
                "max_concurrency": max_concurrency,
                "revision": OriginRateBucket.revision + 1,
                "updated_at": current,
            },
        )
        .returning(OriginRateBucket)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def get_bucket(session: AsyncSession, origin: str) -> OriginRateBucket | None:
    return await session.get(OriginRateBucket, origin)


async def acquire_permit(
    session: AsyncSession,
    origin: str,
    owner_instance: str,
    lease_seconds: int,
    *,
    job_id: UUID | None = None,
    now: datetime | None = None,
) -> PermitDecision:
    """Атомарно видає leased permit (rate token + concurrency slot) або відмовляє.

    Transaction boundary: викликач, **окрема коротка транзакція** — row lock на bucket
    тримається до commit, і лише commit робить permit видимим іншим replicas.
    Bucket відсутній → `NotFoundError` (limiter не вгадує policy — її задає scheduler через
    `ensure_bucket`).
    """
    if lease_seconds < 1:
        msg = "lease_seconds має бути >= 1"
        raise ValueError(msg)
    current = resolve_now(now)
    bucket = await session.scalar(
        select(OriginRateBucket).where(OriginRateBucket.origin == origin).with_for_update()
    )
    if bucket is None:
        msg = f"origin bucket {origin!r} не знайдено"
        raise NotFoundError(msg)

    if bucket.blocked_until is not None and bucket.blocked_until > current:
        return PermitDecision(
            granted=False,
            reason="blocked",
            retry_after=bucket.blocked_until,
            available_tokens=bucket.available_tokens,
        )

    tokens = _refilled_tokens(bucket, current)
    bucket.available_tokens = tokens
    bucket.last_refill_at = current
    bucket.updated_at = current

    live = await session.scalar(
        select(func.count())
        .select_from(OriginRatePermit)
        .where(
            OriginRatePermit.origin == origin,
            OriginRatePermit.released_at.is_(None),
            OriginRatePermit.lease_expires_at > current,
        )
    )
    live_count = int(live or 0)

    if tokens < 1:
        deficit = Decimal(1) - tokens
        wait_seconds = float(deficit / bucket.refill_per_second)
        await session.flush()
        return PermitDecision(
            granted=False,
            reason="rate",
            retry_after=current + timedelta(seconds=wait_seconds),
            live_permits=live_count,
            available_tokens=tokens,
        )
    if live_count >= bucket.max_concurrency:
        earliest = await session.scalar(
            select(func.min(OriginRatePermit.lease_expires_at)).where(
                OriginRatePermit.origin == origin,
                OriginRatePermit.released_at.is_(None),
                OriginRatePermit.lease_expires_at > current,
            )
        )
        await session.flush()
        return PermitDecision(
            granted=False,
            reason="concurrency",
            retry_after=earliest or current,
            live_permits=live_count,
            available_tokens=tokens,
        )

    bucket.available_tokens = tokens - 1
    permit = OriginRatePermit(
        permit_id=new_entity_id(),
        origin=origin,
        owner_instance=owner_instance,
        job_id=job_id,
        acquired_at=current,
        lease_expires_at=current + timedelta(seconds=lease_seconds),
        created_at=current,
    )
    session.add(permit)
    await session.flush()
    return PermitDecision(
        granted=True,
        permit=permit,
        live_permits=live_count + 1,
        available_tokens=bucket.available_tokens,
    )


async def release_permit(
    session: AsyncSession, permit_id: UUID, *, now: datetime | None = None
) -> bool:
    """Повертає concurrency slot; ідемпотентно: `True` лише при першому release, `False` для
    уже released/expired/невідомого permit. Transaction boundary: викликач."""
    current = resolve_now(now)
    released = await session.scalar(
        update(OriginRatePermit)
        .where(OriginRatePermit.permit_id == permit_id, OriginRatePermit.released_at.is_(None))
        .values(released_at=current, release_reason="released")
        .returning(OriginRatePermit.permit_id)
    )
    return released is not None


async def expire_permits(
    session: AsyncSession, *, origin: str | None = None, now: datetime | None = None
) -> int:
    """Позначає прострочені живі permits як `expired` (crash worker без release); повертає
    кількість. Ідемпотентно; maintenance tick. Transaction boundary: викликач."""
    current = resolve_now(now)
    stmt = (
        update(OriginRatePermit)
        .where(
            OriginRatePermit.released_at.is_(None),
            OriginRatePermit.lease_expires_at <= current,
        )
        .values(released_at=current, release_reason="expired")
        .returning(OriginRatePermit.permit_id)
    )
    if origin is not None:
        stmt = stmt.where(OriginRatePermit.origin == origin)
    return len((await session.execute(stmt)).scalars().all())


async def block_origin(
    session: AsyncSession,
    origin: str,
    until: datetime,
    *,
    reason: str,
    now: datetime | None = None,
) -> OriginRateBucket:
    """429/`Retry-After`/challenge: жоден permit до `until` (не скорочує вже довший block).
    Transaction boundary: викликач."""
    current = resolve_now(now)
    bucket = await session.scalar(
        select(OriginRateBucket).where(OriginRateBucket.origin == origin).with_for_update()
    )
    if bucket is None:
        msg = f"origin bucket {origin!r} не знайдено"
        raise NotFoundError(msg)
    if bucket.blocked_until is None or bucket.blocked_until < until:
        bucket.blocked_until = until
        bucket.block_reason = reason[:256]
        bucket.revision += 1
        bucket.updated_at = current
        await session.flush()
    return bucket


async def live_permit_count(
    session: AsyncSession, origin: str, *, now: datetime | None = None
) -> int:
    """Кількість живих permits (діагностика/метрики §14.1)."""
    current = resolve_now(now)
    count = await session.scalar(
        select(func.count())
        .select_from(OriginRatePermit)
        .where(
            OriginRatePermit.origin == origin,
            OriginRatePermit.released_at.is_(None),
            OriginRatePermit.lease_expires_at > current,
        )
    )
    return int(count or 0)


def _refilled_tokens(bucket: OriginRateBucket, now: datetime) -> Decimal:
    elapsed = Decimal(str(max((now - bucket.last_refill_at).total_seconds(), 0.0)))
    refilled = bucket.available_tokens + elapsed * bucket.refill_per_second
    return min(refilled, bucket.capacity_tokens).quantize(TOKEN_QUANTUM, rounding=ROUND_DOWN)
