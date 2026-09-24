"""Transactional outbox publisher API (§7.3, §10 п.13; R-30).

Операції: `fetch_unpublished`, `mark_published`, `mark_failed`, `list_parked`, `unpark`,
`purge_published`, `get_event`, `count_backlog`, `oldest_unpublished_age`.

Доставка — **щонайменше один раз**. Цикл publisher-а:

1. `fetch_unpublished` — коротка транзакція: `FOR UPDATE SKIP LOCKED` + **visibility lease**
   (gate 3, CR-2): вибраним рядкам `available_at` зсувається на `visibility_seconds` уперед і
   транзакція комітиться. Інший publisher не отримає ті самі рядки ні паралельно (row locks),
   ні після commit (до спливу lease). Якщо publisher упав, рядки знову стають видимими після
   lease — звідси «щонайменше один раз». У тій самій транзакції рахуються **видачі**
   (`delivery_attempts`, PR3a N-2): рядок, виданий `max_delivery_attempts` разів, при наступній
   вибірці паркується замість видачі — crash/OOM publisher-а на конкретній події до
   `mark_failed` більше не крутить її нескінченно;
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
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import JsonObject
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import InvalidTransitionError, NotFoundError
from collector.persistence.postgres.models import (
    OutboxEvent,
    ProjectionAcknowledgement,
    ProjectionTask,
)
from collector.persistence.postgres.repositories.audit import append_audit, require_audit_context
from collector.persistence.postgres.repositories.queue import BackoffPolicy

DOMAIN_TOPIC = "domain"
INTERNAL_TOPIC = "internal"
DEFAULT_VISIBILITY_SECONDS = 60
DEFAULT_MAX_PUBLISH_ATTEMPTS = 10
DELIVERY_ATTEMPTS_EXHAUSTED = "delivery_attempts_exhausted"
"""`last_error_code` рядка, запаркованого `fetch_unpublished` за лічильником видач."""


async def fetch_unpublished(
    session: AsyncSession,
    *,
    topics: Sequence[str] = (DOMAIN_TOPIC,),
    limit: int = 100,
    visibility_seconds: int = DEFAULT_VISIBILITY_SECONDS,
    max_delivery_attempts: int = DEFAULT_MAX_PUBLISH_ATTEMPTS,
    now: datetime | None = None,
) -> list[OutboxEvent]:
    """Бере до `limit` готових до доставки рядків і ховає їх на `visibility_seconds`.

    **Лічильник видач (PR3a п.2, N-2 варіант 2).** Одним UPDATE над вибраними рядками:

    - `delivery_attempts < max_delivery_attempts` → `delivery_attempts + 1`, visibility lease;
      рядок повертається;
    - `delivery_attempts >= max_delivery_attempts` → `parked_at = now`, `last_error_code =
      "delivery_attempts_exhausted"`; рядок **не** повертається (видно в `list_parked`).

    Тобто подія видається щонайбільше `max_delivery_attempts` разів, навіть якщо publisher
    щоразу падає до `mark_failed`. Запаркований рядок звільняє місце: наступний виклик бере
    наступні події (немає head-of-line blocking), але поточний батч може бути коротшим за
    `limit` або навіть порожнім, якщо всі вибрані кандидати саме вичерпали межу. Тому `[]`
    означає «у цій транзакції нічого не видано», а не гарантує порожній backlog; publisher
    опитує знову за звичайним poll-інтервалом. `mark_failed` рахує лише помилки (`attempts`) і
    `delivery_attempts` не чіпає, тож одна спроба не рахується двічі; межі незалежні, паркує
    та, що спрацює першою.

    Предикат — `published_at IS NULL AND parked_at IS NULL AND available_at <= now` з порядком
    `(available_at, event_id)`, тобто partial index `ix_outbox_events_unpublished` (і
    обов'язковий §9.1 `outbox_events(published_at, available_at, event_id)`).

    Transaction boundary: викликач, **коротка окрема транзакція** — commit одразу після
    виклику, доставка поза нею (інакше lease і locks тримаються весь час доставки).
    """
    if limit < 1 or visibility_seconds < 1 or max_delivery_attempts < 1:
        msg = "limit, visibility_seconds і max_delivery_attempts мають бути >= 1"
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
    exhausted = OutboxEvent.delivery_attempts >= max_delivery_attempts
    leased = list(
        (
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.outbox_id.in_(candidates))
                .values(
                    available_at=case(
                        (exhausted, OutboxEvent.available_at),
                        else_=current + timedelta(seconds=visibility_seconds),
                    ),
                    delivery_attempts=case(
                        (exhausted, OutboxEvent.delivery_attempts),
                        else_=OutboxEvent.delivery_attempts + 1,
                    ),
                    parked_at=case((exhausted, current), else_=OutboxEvent.parked_at),
                    last_error_code=case(
                        (exhausted, DELIVERY_ATTEMPTS_EXHAUSTED),
                        else_=OutboxEvent.last_error_code,
                    ),
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
    delivered = [event for event in leased if event.parked_at is None]
    delivered.sort(key=lambda event: (event.created_at, event.event_id))
    return delivered


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
    """Операторське повернення припаркованого рядка в доставку (`attempts = 0`,
    `delivery_attempts = 0`) + audit.

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
    before: JsonObject = {
        "parked_at": event.parked_at.isoformat(),
        "attempts": event.attempts,
        "delivery_attempts": event.delivery_attempts,
        "last_error_code": event.last_error_code,
    }
    event.parked_at = None
    event.attempts = 0
    event.delivery_attempts = 0
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
        after={"parked_at": None, "attempts": 0, "delivery_attempts": 0, "reason": reason},
        request_id=request_id,
        now=current,
    )
    return event


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """Підсумок одного батча `purge_published`."""

    deleted: int
    last_event_id: UUID | None
    """Найбільший `event_id` цього батча — `after` для наступного виклику; `None` — кандидатів
    більше немає (прохід завершено)."""


async def purge_published(
    session: AsyncSession,
    *,
    older_than: timedelta,
    after: UUID | None = None,
    limit: int = 1000,
    now: datetime | None = None,
) -> PurgeResult:
    """Retention `outbox_events` (ADR-0007; PR3a п.3): видаляє до `limit` рядків, яким уже не
    потрібна доставка, keyset за `event_id` (без `OFFSET`).

    Кандидати (`cutoff = now - older_than`):

    - `topic='domain'` — опубліковані: `published_at IS NOT NULL AND published_at < cutoff`.
      Неопублікований або припаркований рядок не видаляється ніколи — «щонайменше один раз»;
    - `topic='internal'` (`projection.command`, ніколи не публікується, fail-closed) — лише
      якщо його task (`projection_tasks.task_id = event_id`) у статусі `succeeded` **і** має
      `projection_acknowledgements`, а рядок створено раніше за `cutoff`. Task не acknowledged
      (pending/retry/leased) або `quarantined` → рядок лишається: команда ще потрібна для
      replay/reconcile.

    Рядки, заблоковані іншою транзакцією (publisher саме їх видає), пропускаються
    (`SKIP LOCKED`). Повторний виклик ідемпотентний. Transaction boundary: викликач (коротка
    транзакція на батч; maintenance WP-12 під `collector_scheduler` повторює з
    `after=last_event_id`, доки `last_event_id` не стане `None`).
    """
    if limit < 1:
        msg = "limit має бути >= 1"
        raise ValueError(msg)
    if older_than < timedelta(0):
        msg = "older_than не може бути від'ємним"
        raise ValueError(msg)
    current = resolve_now(now)
    cutoff = current - older_than
    acknowledged = (
        select(ProjectionTask.task_id)
        .join(
            ProjectionAcknowledgement,
            ProjectionAcknowledgement.task_id == ProjectionTask.task_id,
        )
        .where(
            ProjectionTask.task_id == OutboxEvent.event_id,
            ProjectionTask.status == "succeeded",
        )
        .exists()
    )
    candidates = (
        select(OutboxEvent.outbox_id, OutboxEvent.event_id)
        .where(
            or_(
                and_(
                    OutboxEvent.topic == DOMAIN_TOPIC,
                    OutboxEvent.published_at.is_not(None),
                    OutboxEvent.published_at < cutoff,
                ),
                and_(
                    OutboxEvent.topic == INTERNAL_TOPIC,
                    OutboxEvent.created_at < cutoff,
                    acknowledged,
                ),
            )
        )
        .order_by(OutboxEvent.event_id)
        .limit(limit)
        .with_for_update(of=OutboxEvent, skip_locked=True)
    )
    if after is not None:
        candidates = candidates.where(OutboxEvent.event_id > after)
    rows = (await session.execute(candidates)).all()
    if not rows:
        return PurgeResult(deleted=0, last_event_id=None)
    result = await session.execute(
        delete(OutboxEvent)
        .where(OutboxEvent.outbox_id.in_([row.outbox_id for row in rows]))
        .returning(OutboxEvent.outbox_id)
    )
    deleted = len(result.scalars().all())
    return PurgeResult(deleted=deleted, last_event_id=max(row.event_id for row in rows))


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
    session: AsyncSession,
    *,
    topics: Sequence[str] = (DOMAIN_TOPIC,),
    now: datetime | None = None,
) -> timedelta | None:
    """Вік найстарішої недоставленої події — джерело алерту §14.2; `None`, якщо backlog порожній.

    Предикат узгоджено з `count_backlog` (gate 4, N-3): не опубліковано й не припарковано,
    лише задані топіки (типово `domain`). Внутрішні `projection.command` publisher не
    публікує, тож без фільтра вік ріс би безмежно і алерт горів би завжди; припарковані
    рядки мають власний сигнал (`list_parked`).
    """
    if not topics:
        msg = "topics не може бути порожнім"
        raise ValueError(msg)
    current = resolve_now(now)
    oldest = await session.scalar(
        select(func.min(OutboxEvent.created_at)).where(
            OutboxEvent.published_at.is_(None),
            OutboxEvent.parked_at.is_(None),
            OutboxEvent.topic.in_(list(topics)),
        )
    )
    return None if oldest is None else current - oldest


def _truncate(message: str | None, limit: int = 2048) -> str | None:
    if message is None:
        return None
    return message if len(message) <= limit else message[: limit - 1] + "…"


__all__ = [
    "DEFAULT_MAX_PUBLISH_ATTEMPTS",
    "DEFAULT_VISIBILITY_SECONDS",
    "DELIVERY_ATTEMPTS_EXHAUSTED",
    "DOMAIN_TOPIC",
    "INTERNAL_TOPIC",
    "PurgeResult",
    "count_backlog",
    "fetch_unpublished",
    "get_event",
    "list_parked",
    "mark_failed",
    "mark_published",
    "oldest_unpublished_age",
    "purge_published",
    "unpark",
]
