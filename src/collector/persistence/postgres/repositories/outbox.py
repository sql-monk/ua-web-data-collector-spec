"""Transactional outbox publisher API (§7.3, §10 п.13; R-30).

Операції: `fetch_unpublished`, `mark_published`, `mark_failed`, `list_parked`, `unpark`,
`get_event`, `count_backlog`, `oldest_unpublished_age`.

Доставка — **щонайменше один раз**. Цикл publisher-а:

1. `fetch_unpublished` — коротка транзакція: `FOR UPDATE SKIP LOCKED` + **visibility lease**
   (gate 3, CR-2): вибраним рядкам `available_at` зсувається на `visibility_seconds` уперед і
   транзакція комітиться. Інший publisher не отримає ті самі рядки ні паралельно (row locks),
   ні після commit (до спливу lease). Якщо publisher упав, рядки знову стають видимими після
   lease — звідси «щонайменше один раз»;
2. доставка поза транзакцією;
3. `mark_published` або `mark_failed` (backoff; після `max_attempts` рядок **паркується** —
   `parked_at`, і далі не видається, доки оператор не зробить `unpark`, CR-3).

Consumer зобов'язаний бути ідемпотентним за `event_id` (§7.3).

`projection.command` (топік `internal`) і `domain.changed` (топік `domain`) лежать в одній
таблиці. `fetch_unpublished` за замовчуванням віддає **лише** `domain` (fail-closed, S-6):
внутрішня команда назовні не публікується (R-30), а маршрутизація йде за `topic`, не за
`event_type`.

Transaction boundary усіх функцій — викликач.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import JsonObject
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import InvalidTransitionError, NotFoundError
from collector.persistence.postgres.models import OutboxEvent
from collector.persistence.postgres.repositories.audit import append_audit, require_audit_context
from collector.persistence.postgres.repositories.queue import BackoffPolicy

DOMAIN_TOPIC = "domain"
INTERNAL_TOPIC = "internal"
DEFAULT_VISIBILITY_SECONDS = 60
DEFAULT_MAX_PUBLISH_ATTEMPTS = 10


async def fetch_unpublished(
    session: AsyncSession,
    *,
    topics: Sequence[str] = (DOMAIN_TOPIC,),
    limit: int = 100,
    visibility_seconds: int = DEFAULT_VISIBILITY_SECONDS,
    now: datetime | None = None,
) -> list[OutboxEvent]:
    """Бере до `limit` готових до доставки рядків і ховає їх на `visibility_seconds`.

    Предикат — `published_at IS NULL AND parked_at IS NULL AND available_at <= now` з порядком
    `(available_at, event_id)`, тобто partial index `ix_outbox_events_unpublished` (і
    обов'язковий §9.1 `outbox_events(published_at, available_at, event_id)`).

    Transaction boundary: викликач, **коротка окрема транзакція** — commit одразу після
    виклику, доставка поза нею (інакше lease і locks тримаються весь час доставки).
    """
    if limit < 1 or visibility_seconds < 1:
        msg = "limit і visibility_seconds мають бути >= 1"
        raise ValueError(msg)
    if not topics:
        msg = "topics не може бути порожнім"
        raise ValueError(msg)
    current = resolve_now(now)
    candidates = (
        select(OutboxEvent.outbox_id)
        .where(
            OutboxEvent.published_at.is_(None),
            OutboxEvent.parked_at.is_(None),
            OutboxEvent.available_at <= current,
            OutboxEvent.topic.in_(list(topics)),
        )
        .order_by(OutboxEvent.available_at, OutboxEvent.event_id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    leased = list(
        (
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.outbox_id.in_(candidates))
                .values(
                    available_at=current + timedelta(seconds=visibility_seconds),
                    updated_at=current,
                )
                .returning(OutboxEvent)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    # UPDATE … RETURNING не гарантує порядку; `available_at` уже зсунуто — сортуємо за часом
    # створення (для рядків без backoff це той самий порядок, що й вибірка).
    leased.sort(key=lambda event: (event.created_at, event.event_id))
    return leased


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
    max_attempts: int = DEFAULT_MAX_PUBLISH_ATTEMPTS,
    now: datetime | None = None,
) -> OutboxEvent:
    """Невдала доставка: `attempts + 1`, помилка і новий `available_at` за backoff.

    Після `max_attempts` рядок паркується (`parked_at = now`, CR-3): publisher його більше не
    бере, а оператор бачить його в `list_parked` (алерт `outbox_backlog`, §14.2) і вирішує —
    `unpark` після усунення причини. Рядок ніколи не видаляється: доставка «щонайменше один
    раз» не має права мовчки загубити подію.
    """
    if max_attempts < 1:
        msg = "max_attempts має бути >= 1"
        raise ValueError(msg)
    current = resolve_now(now)
    event = await session.get(OutboxEvent, outbox_id, with_for_update=True)
    if event is None:
        msg = f"outbox event {outbox_id} не знайдено"
        raise NotFoundError(msg)
    if event.published_at is not None or event.parked_at is not None:
        return event
    event.attempts += 1
    event.last_error_code = error_code
    event.last_error_message = _truncate(error_message)
    event.updated_at = current
    if event.attempts >= max_attempts:
        event.parked_at = current
    else:
        delay = (policy or BackoffPolicy()).delay_for(event.attempts, random.SystemRandom())
        event.available_at = current + delay
    await session.flush()
    return event


async def list_parked(session: AsyncSession, *, limit: int = 100) -> list[OutboxEvent]:
    """Припарковані рядки для оператора (найстаріші першими)."""
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.parked_at.is_not(None), OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.parked_at, OutboxEvent.event_id)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def unpark(
    session: AsyncSession,
    outbox_id: UUID,
    *,
    actor: str,
    reason: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> OutboxEvent:
    """Операторське повернення припаркованого рядка в доставку (`attempts = 0`) + audit.

    Transaction boundary: викликач; audit — у тій самій транзакції (§13).
    """
    require_audit_context(actor, reason)
    current = resolve_now(now)
    event = await session.get(OutboxEvent, outbox_id, with_for_update=True)
    if event is None:
        msg = f"outbox event {outbox_id} не знайдено"
        raise NotFoundError(msg)
    if event.parked_at is None or event.published_at is not None:
        msg = f"outbox event {outbox_id} не припаркований"
        raise InvalidTransitionError(msg)
    before: JsonObject = {"parked_at": event.parked_at.isoformat(), "attempts": event.attempts}
    event.parked_at = None
    event.attempts = 0
    event.available_at = current
    event.updated_at = current
    await session.flush()
    await append_audit(
        session,
        actor=actor,
        action="outbox.unpark",
        resource_type="outbox_event",
        resource_id=str(event.event_id),
        before=before,
        after={"parked_at": None, "attempts": 0, "reason": reason},
        request_id=request_id,
        now=current,
    )
    return event


async def get_event(session: AsyncSession, event_id: UUID) -> OutboxEvent | None:
    """Рядок за глобально unique `event_id` (не за PK) — саме ним дедуплікує consumer."""
    stmt = select(OutboxEvent).where(OutboxEvent.event_id == event_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_backlog(session: AsyncSession, *, topic: str | None = None) -> int:
    """Скільки подій ще не доставлено (метрика `outbox_backlog`, §14.1).

    Рахує і рядки в польоті (visibility lease), і рядки в backoff — усе, що не опубліковано й
    не припарковано; припарковані рахує окремо `list_parked`.
    """
    stmt = (
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None), OutboxEvent.parked_at.is_(None))
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
    "DEFAULT_MAX_PUBLISH_ATTEMPTS",
    "DEFAULT_VISIBILITY_SECONDS",
    "DOMAIN_TOPIC",
    "INTERNAL_TOPIC",
    "count_backlog",
    "fetch_unpublished",
    "get_event",
    "list_parked",
    "mark_failed",
    "mark_published",
    "oldest_unpublished_age",
    "unpark",
]
