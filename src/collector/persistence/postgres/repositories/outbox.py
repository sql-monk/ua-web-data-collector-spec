"""Transactional outbox publisher API (§7.3, §10 п.13; R-30).

Операції: `fetch_unpublished`, `mark_published`, `mark_failed`, `get_event`, `count_backlog`.

Доставка — **щонайменше один раз**: publisher читає неопубліковані рядки, віддає їх
споживачу і лише після підтвердження ставить `published_at`. Consumer зобов'язаний бути
ідемпотентним за `event_id` (§7.3) — саме тому `event_id` тут глобально unique і ніколи не
перевикористовується.

`projection.command` (топік `internal`) і `domain.changed` (топік `domain`) лежать в одній
таблиці, але publisher зовнішніх подій зобов'язаний фільтрувати `topic='domain'`: внутрішня
команда projector-а назовні не публікується (R-30).

Transaction boundary усіх функцій — викликач; `fetch_unpublished` тримає row locks до commit,
тому має бути короткою транзакцією (claim → commit → доставка поза транзакцією).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import NotFoundError
from collector.persistence.postgres.models import OutboxEvent
from collector.persistence.postgres.repositories.queue import BackoffPolicy

DOMAIN_TOPIC = "domain"
INTERNAL_TOPIC = "internal"


async def fetch_unpublished(
    session: AsyncSession,
    *,
    limit: int = 100,
    topics: Sequence[str] | None = None,
    lock: bool = True,
    now: datetime | None = None,
) -> list[OutboxEvent]:
    """Неопубліковані рядки, готові до доставки (`available_at <= now`), у порядку index.

    Порядок і предикат збігаються з обов'язковим index §9.1
    `outbox_events(published_at, available_at, event_id)` і з partial-index-ом
    `ix_outbox_events_unpublished` (`WHERE published_at IS NULL`), який обслуговує hot path.

    `lock=True` (типово) додає `FOR UPDATE SKIP LOCKED`: кілька publisher-ів можуть працювати
    паралельно, не віддаючи один одному ті самі події. Тоді виклик **зобов'язаний** бути
    короткою транзакцією — locks тримаються до commit.
    """
    if limit < 1:
        msg = "limit має бути >= 1"
        raise ValueError(msg)
    current = resolve_now(now)
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None), OutboxEvent.available_at <= current)
        .order_by(OutboxEvent.available_at, OutboxEvent.event_id)
        .limit(limit)
    )
    if topics is not None:
        stmt = stmt.where(OutboxEvent.topic.in_(list(topics)))
    if lock:
        stmt = stmt.with_for_update(skip_locked=True)
    return list((await session.execute(stmt)).scalars().all())


async def mark_published(
    session: AsyncSession, outbox_ids: Sequence[UUID], *, now: datetime | None = None
) -> int:
    """Позначає доставлені рядки; повторний виклик не змінює вже проставлений `published_at`.

    Повертає кількість рядків, позначених саме цим викликом (0 — усі вже були опубліковані).
    """
    if not outbox_ids:
        return 0
    current = resolve_now(now)
    result = await session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.outbox_id.in_(list(outbox_ids)),
            OutboxEvent.published_at.is_(None),
        )
        .values(
            published_at=current, last_error_code=None, last_error_message=None, updated_at=current
        )
        .returning(OutboxEvent.outbox_id)
    )
    return len(result.scalars().all())


async def mark_failed(
    session: AsyncSession,
    outbox_id: UUID,
    *,
    error_code: str,
    error_message: str | None = None,
    policy: BackoffPolicy | None = None,
    now: datetime | None = None,
) -> OutboxEvent:
    """Невдала доставка: `attempts + 1`, помилка і новий `available_at` за backoff.

    Backoff — та сама політика, що й у черзі jobs (`queue.BackoffPolicy`), щоб недоступний
    consumer не отримував ретраї частіше, ніж недоступне джерело. Рядок **не** видаляється і
    не «отруюється»: outbox доставляє щонайменше один раз, тож остаточне рішення про подію,
    яку неможливо доставити, ухвалює оператор (§14.2 алерт `outbox_backlog`).
    """
    current = resolve_now(now)
    event = await session.get(OutboxEvent, outbox_id, with_for_update=True)
    if event is None:
        msg = f"outbox event {outbox_id} не знайдено"
        raise NotFoundError(msg)
    if event.published_at is not None:
        return event
    event.attempts += 1
    delay = (policy or BackoffPolicy()).delay_for(event.attempts, random.SystemRandom())
    event.available_at = current + delay
    event.last_error_code = error_code
    event.last_error_message = _truncate(error_message)
    event.updated_at = current
    await session.flush()
    return event


async def get_event(session: AsyncSession, event_id: UUID) -> OutboxEvent | None:
    """Рядок за глобально unique `event_id` (не за PK) — саме ним дедуплікує consumer."""
    stmt = select(OutboxEvent).where(OutboxEvent.event_id == event_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_backlog(
    session: AsyncSession, *, topic: str | None = None, now: datetime | None = None
) -> int:
    """Скільки подій чекають доставки (метрика `outbox_backlog`, §14.1)."""
    current = resolve_now(now)
    stmt = (
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None), OutboxEvent.available_at <= current)
    )
    if topic is not None:
        stmt = stmt.where(OutboxEvent.topic == topic)
    return int(await session.scalar(stmt) or 0)


async def oldest_unpublished_age(
    session: AsyncSession, *, now: datetime | None = None
) -> timedelta | None:
    """Вік найстарішої недоставленої події — джерело алерту §14.2; `None`, якщо backlog порожній."""
    current = resolve_now(now)
    oldest = await session.scalar(
        select(func.min(OutboxEvent.created_at)).where(OutboxEvent.published_at.is_(None))
    )
    return None if oldest is None else current - oldest


def _truncate(message: str | None, limit: int = 2048) -> str | None:
    if message is None:
        return None
    return message if len(message) <= limit else message[: limit - 1] + "…"


__all__ = [
    "DOMAIN_TOPIC",
    "INTERNAL_TOPIC",
    "count_backlog",
    "fetch_unpublished",
    "get_event",
    "mark_failed",
    "mark_published",
    "oldest_unpublished_age",
]
