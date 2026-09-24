"""Job queue §7.2: enqueue / claim (`FOR UPDATE SKIP LOCKED`) / heartbeat / complete / retry /
quarantine / recover_expired_leases / dead letters.

Transaction boundaries:

- `claim` тримає row locks на вибраних jobs до commit — викликач робить commit одразу після
  claim і виконує роботу поза транзакцією;
- `retry`/`quarantine` пишуть `dead_letters` у тій самій транзакції, що й зміну статусу;
- решта — один UPDATE із предикатом owner; викликач може об'єднувати їх з іншими записами.

Claim і indexes: `claim` читає `ix_crawl_jobs_claimable_order` — partial index
`(priority DESC, not_before, job_id) WHERE status IN ('pending','retry')` у точному порядку
`ORDER BY` (міграція `0002_claim_index`); обов'язковий за карткою
`ix_crawl_jobs_status_not_before_priority` лишається для операторських вибірок за
`(status, not_before)`. `FOR UPDATE SKIP LOCKED` — не оптимізація, а вимога §7.2: без нього
claimers серіалізуються на зайнятих рядках (тест `test_queue.py::
test_claim_uses_skip_locked_and_does_not_block_on_rows_locked_by_another_claimer`).

Lease-семантика: `heartbeat`/`complete`/`retry` виконуються лише для `status='leased' AND
lease_owner=:owner`. Прострочений, але ще не відновлений lease власник може продовжити
(нікому іншому job не належить); після `recover_expired_leases` або claim іншим worker owner
не збігається → `LeaseNotOwnedError`.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import new_entity_id
from collector.persistence.postgres.clock import require_aware_utc, resolve_now
from collector.persistence.postgres.errors import (
    LeaseNotOwnedError,
    NotFoundError,
)
from collector.persistence.postgres.models import (
    CLAIMABLE_JOB_STATUSES,
    CrawlJob,
    DeadLetter,
)
from collector.persistence.postgres.repositories.audit import append_audit, require_audit_context

TERMINAL_JOB_STATUSES: frozenset[str] = frozenset({"succeeded", "quarantined"})


@dataclass(frozen=True, slots=True)
class NewJob:
    """Параметри enqueue; `idempotency_key` — §9.3 п.3 (`fetch_idempotency_key`) або інший
    детермінований ключ job_type-у."""

    job_type: str
    idempotency_key: str
    args: Mapping[str, object] = field(default_factory=dict)
    priority: int = 100
    max_attempts: int = 5
    not_before: datetime | None = None
    run_id: UUID | None = None
    source_id: UUID | None = None


MAX_BACKOFF_EXPONENT = 63


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Експоненційний backoff з jitter: `base * multiplier**(attempt-1)`, рівномірний jitter у
    `[0, delay * jitter_ratio]`, і **весь результат** обмежений `maximum`.

    Cap застосовується після jitter (L-6 код-рев'ю): інакше фактична межа була б
    `maximum * (1 + jitter_ratio)`, і `not_before` виходив би за обіцяні `maximum`.
    """

    base: timedelta = timedelta(seconds=30)
    multiplier: float = 2.0
    maximum: timedelta = timedelta(hours=6)
    jitter_ratio: float = 0.2

    def delay_for(self, attempt: int, rng: random.Random) -> timedelta:
        # Exponent обмежено ДО піднесення (gate 3, CR-3): `2.0 ** 1025` — OverflowError, тобто
        # outbox/черга з тисячами спроб падали б замість віддати `maximum`. 2**63 × base уже
        # на порядки більше будь-якого розумного `maximum`, тож cap нижче значення не змінює.
        exponent = min(max(attempt - 1, 0), MAX_BACKOFF_EXPONENT)
        maximum = self.maximum.total_seconds()
        delay = min(self.base.total_seconds() * (self.multiplier**exponent), maximum)
        jitter = rng.uniform(0.0, delay * self.jitter_ratio) if self.jitter_ratio > 0 else 0.0
        return timedelta(seconds=min(delay + jitter, maximum))


def clamp_not_before(not_before: datetime | None, now: datetime) -> datetime:
    """`max(not_before, now)` у UTC; naive datetime відхиляється до будь-якого запису."""
    current = require_aware_utc(now, parameter="now")
    if not_before is None:
        return current
    lower_bound = require_aware_utc(not_before, parameter="not_before")
    return current if lower_bound < current else lower_bound


def next_attempt_at(
    now: datetime,
    attempt: int,
    *,
    policy: BackoffPolicy | None = None,
    rng: random.Random | None = None,
    not_before: datetime | None = None,
) -> datetime:
    """Час наступної спроби для `retry`: явний `not_before` (стиснутий до `now`) або
    `now + BackoffPolicy.delay_for(attempt)` — див. docstring `retry`."""
    current = require_aware_utc(now, parameter="now")
    if not_before is not None:
        return clamp_not_before(not_before, current)
    return current + (policy or BackoffPolicy()).delay_for(attempt, rng or random.SystemRandom())


async def enqueue(session: AsyncSession, job: NewJob, *, now: datetime | None = None) -> CrawlJob:
    """Ставить job у чергу; повторний виклик з тим самим `idempotency_key` повертає існуючий
    job без дубля (`INSERT ... ON CONFLICT DO NOTHING` + SELECT).

    **Ключ — це ідентичність job, а не запит «постав у чергу знову»:** якщо job із цим ключем
    уже `succeeded`/`quarantined`, повертається саме він, і нової роботи не з'явиться. Тому
    ключ має містити дискримінатор циклу/вікна (§9.3 п.3 — `planned_at_bucket`), інакше після
    першого успішного обходу джерело більше ніколи не фетчиться, без жодної помилки (L-8
    код-рев'ю). Статус повернутого job викликач перевіряє сам.

    Transaction boundary: викликач. **Вимога до isolation level: READ COMMITTED** (default
    PostgreSQL). Ідемпотентність тримається на тому, що після `ON CONFLICT DO NOTHING` наступний
    `SELECT` бере свіжий snapshot і бачить рядок, закомічений конкурентною транзакцією. У
    `REPEATABLE READ`/`SERIALIZABLE` конкурентний commit того самого ключа робить
    `ON CONFLICT DO NOTHING` несеріалізовним і PostgreSQL кидає `could not serialize access due
    to concurrent update` — транзакцію доведеться повторити цілком. Дубля при цьому не
    виникає, але викликач не має відкривати транзакцію з enqueue у вищому рівні ізоляції
    (перевірено `test_queue.py::
    test_enqueue_outside_read_committed_fails_loudly_without_duplicating`).
    """
    current = resolve_now(now)
    values = {
        "job_id": new_entity_id(),
        "run_id": job.run_id,
        "source_id": job.source_id,
        "job_type": job.job_type,
        "status": "pending",
        "priority": job.priority,
        "idempotency_key": job.idempotency_key,
        "args": dict(job.args),
        "attempt": 0,
        "max_attempts": job.max_attempts,
        "not_before": job.not_before or current,
        "created_at": current,
        "updated_at": current,
    }
    stmt = (
        pg_insert(CrawlJob)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[CrawlJob.idempotency_key])
        .returning(CrawlJob)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is not None:
        return inserted
    existing = await session.scalar(
        select(CrawlJob)
        .where(CrawlJob.idempotency_key == job.idempotency_key)
        .execution_options(populate_existing=True)
    )
    if existing is None:
        msg = (
            f"job з idempotency_key={job.idempotency_key!r} не видно після ON CONFLICT: "
            "транзакція має бути READ COMMITTED (див. docstring enqueue)"
        )
        raise NotFoundError(msg)
    return existing


async def claim(
    session: AsyncSession,
    job_types: Sequence[str],
    worker: str,
    lease_seconds: int,
    *,
    limit: int = 1,
    now: datetime | None = None,
) -> list[CrawlJob]:
    """Захоплює до `limit` jobs типів `job_types`: `status IN (pending, retry) AND not_before <=
    now`, найвищий `priority` перший, `FOR UPDATE SKIP LOCKED`; збільшує `attempt`, ставить
    lease. Transaction boundary: викликач, commit одразу після виклику (row locks)."""
    if not job_types:
        return []
    if limit < 1 or lease_seconds < 1:
        msg = "limit і lease_seconds мають бути >= 1"
        raise ValueError(msg)
    current = resolve_now(now)
    candidates = (
        select(CrawlJob.job_id)
        .where(
            CrawlJob.status.in_(CLAIMABLE_JOB_STATUSES),
            CrawlJob.not_before <= current,
            CrawlJob.job_type.in_(list(job_types)),
        )
        .order_by(CrawlJob.priority.desc(), CrawlJob.not_before, CrawlJob.job_id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    stmt = (
        update(CrawlJob)
        .where(CrawlJob.job_id.in_(candidates))
        .values(
            status="leased",
            lease_owner=worker,
            lease_expires_at=current + timedelta(seconds=lease_seconds),
            leased_at=current,
            attempt=CrawlJob.attempt + 1,
            updated_at=current,
        )
        .returning(CrawlJob)
    )
    claimed = list((await session.execute(stmt)).scalars().all())
    # RETURNING не гарантує порядок підзапиту — впорядкувати як у claim-запиті.
    claimed.sort(key=lambda job: (-job.priority, job.not_before, job.job_id))
    return claimed


async def heartbeat(
    session: AsyncSession,
    job_id: UUID,
    owner: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Продовжує lease власнику до `now + lease_seconds`; чужий/відсутній lease →
    `LeaseNotOwnedError`. Transaction boundary: викликач."""
    current = resolve_now(now)
    expires = current + timedelta(seconds=lease_seconds)
    updated = await session.scalar(
        update(CrawlJob)
        .where(_owned(job_id, owner))
        .values(lease_expires_at=expires, updated_at=current)
        .returning(CrawlJob.job_id)
    )
    if updated is None:
        raise LeaseNotOwnedError(_not_owned_message(job_id, owner))
    return expires


async def complete(
    session: AsyncSession, job_id: UUID, owner: str, *, now: datetime | None = None
) -> CrawlJob:
    """`leased` → `succeeded` лише власником lease. Transaction boundary: викликач (разом із
    результатом роботи — artifact pointer/outbox у тій самій транзакції)."""
    current = resolve_now(now)
    job = (
        await session.execute(
            update(CrawlJob)
            .where(_owned(job_id, owner))
            .values(
                status="succeeded",
                lease_owner=None,
                lease_expires_at=None,
                finished_at=current,
                updated_at=current,
            )
            .returning(CrawlJob)
        )
    ).scalar_one_or_none()
    if job is None:
        raise LeaseNotOwnedError(_not_owned_message(job_id, owner))
    return job


async def retry(
    session: AsyncSession,
    job_id: UUID,
    owner: str,
    *,
    error_code: str,
    error_message: str | None = None,
    policy: BackoffPolicy | None = None,
    rng: random.Random | None = None,
    not_before: datetime | None = None,
    now: datetime | None = None,
) -> CrawlJob:
    """Retryable-помилка: `leased` → `retry`; якщо `attempt >= max_attempts` — `quarantined`
    + `dead_letters(reason=max_attempts)` (незалежно від `not_before`).

    Наступна спроба (`not_before` рядка):

    - `not_before=None` → `now + policy.delay_for(attempt)` (експоненційний `BackoffPolicy`,
      поведінка PR1);
    - `not_before` задано → **рівно** `max(not_before, now)`, `policy` не застосовується
      (PR3a п.1). Викликач передає вже обчислену межу: табличну затримку §10 разом із
      `Retry-After` (runtime WP-01D рахує `max(now + schedule.delay(attempt), retry_after)`).
      Додавати поверх неї ще й дефолтний `BackoffPolicy` не можна — 30 с × 2 перекрили б
      таблицю 5 с/30 с/2 хв/10 хв. Минуле значення стискається до `now` (job claimable
      одразу, без «подорожі в минуле» в порядку claim).

    Transaction boundary: викликач; статус і dead letter — одна транзакція."""
    current = resolve_now(now)
    job = await _lock_owned(session, job_id, owner)
    if job.attempt >= job.max_attempts:
        return await _quarantine_locked(
            session,
            job,
            reason="max_attempts",
            error_code=error_code,
            error_message=error_message,
            now=current,
        )
    job.status = "retry"
    job.lease_owner = None
    job.lease_expires_at = None
    job.not_before = next_attempt_at(
        current, job.attempt, policy=policy, rng=rng, not_before=not_before
    )
    job.last_error_code = error_code
    job.last_error_message = _truncate(error_message)
    job.updated_at = current
    await session.flush()
    return job


async def quarantine(
    session: AsyncSession,
    job_id: UUID,
    owner: str | None,
    *,
    error_code: str,
    error_message: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> CrawlJob:
    """Permanent failure або рішення оператора: → `quarantined` + `dead_letters(quarantine)`.

    `owner=None` — **операторський** виклик для будь-якого нетермінального job (без
    lease-перевірки). Саме для нього `actor` і `reason` обов'язкові, і репозиторій пише
    `audit_log` у тій самій транзакції (§13; знахідка S-2 пострев'ю PR1): людина, яка вручну
    знімає job із черги, не може зробити це без сліду. Виклик воркера (`owner` задано)
    аудиту не пише — його слід це `dead_letters` + `last_error_code`, а runtime-ролі парсера
    й фетчера свідомо не мають доступу читати журнал.

    Transaction boundary: викликач.
    """
    current = resolve_now(now)
    if owner is None:
        # Та сама перевірка, що в інших audited-операціях: пробільний actor/reason — не слід.
        require_audit_context(actor, reason)
    job = (
        await _lock_owned(session, job_id, owner)
        if owner is not None
        else await _lock_any(session, job_id)
    )
    before_status = job.status
    quarantined = await _quarantine_locked(
        session,
        job,
        reason="quarantine",
        error_code=error_code,
        error_message=error_message,
        now=current,
    )
    if owner is None and actor is not None:
        await append_audit(
            session,
            actor=actor,
            action="crawl_job.quarantine",
            resource_type="crawl_job",
            resource_id=str(job_id),
            before={"status": before_status, "attempt": quarantined.attempt},
            after={"status": quarantined.status, "error_code": error_code, "reason": reason},
            request_id=request_id,
            now=current,
        )
    return quarantined


async def release(
    session: AsyncSession,
    job_id: UUID,
    owner: str,
    *,
    not_before: datetime | None = None,
    now: datetime | None = None,
) -> CrawlJob:
    """Плановий drain або defer: `leased` → `pending` **без** спалювання спроби і **без**
    помилки.

    Запит WP-01D (`docs/plan/deps/WP-01D-to-WP-01A.md` §3, §5): при scale-down контейнер
    зупиняють, і незавершені jobs треба повернути в чергу негайно. `retry` для цього не
    підходить двічі:

    - на останній спробі (`attempt >= max_attempts`) він відправив би job у `quarantined` з
      dead letter `max_attempts`, хоча її ніхто не провалив;
    - він пише `last_error_code`/`last_error_message` (`drain_timeout`), що вводить в оману
      оператора і псує статистику dead letters (уточнення §5 запиту).

    Тому `release` не пише полів помилки і **компенсує інкремент claim**:
    `attempt = GREATEST(attempt - 1, 0)` (gate 3, CR-5) — спроба, перервана плановим drain, не
    «згоряє» (інакше `max_attempts=2` давав би карантин після однієї справжньої помилки).
    `not_before = now`, тож job стає claimable одразу.

    **Defer (PR3a п.1; WP-01D PR1c `TaskResult.deferred`, WP-02, WP-04):** `not_before`
    задано → job повертається в `pending` з `not_before = max(not_before, now)` і не
    claim-иться раніше. Семантика та сама: `attempt` компенсовано, `last_error_*` не
    змінюються, dead letter не створюється — скільки завгодно defer поспіль не ведуть у
    карантин. Верхню межу defer (clamp) тримає викликач (runtime WP-01D).

    Чужий/відсутній lease → `LeaseNotOwnedError`: повертати чужу job не можна, її вже виконує
    інший instance. Transaction boundary: викликач (одна коротка транзакція на job).
    """
    current = resolve_now(now)
    job = (
        await session.execute(
            update(CrawlJob)
            .where(_owned(job_id, owner))
            .values(
                status="pending",
                lease_owner=None,
                lease_expires_at=None,
                leased_at=None,
                not_before=clamp_not_before(not_before, current),
                attempt=func.greatest(CrawlJob.attempt - 1, 0),
                updated_at=current,
            )
            .returning(CrawlJob)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if job is None:
        raise LeaseNotOwnedError(_not_owned_message(job_id, owner))
    return job


async def recover_expired_leases(
    session: AsyncSession, *, limit: int = 1000, now: datetime | None = None
) -> list[UUID]:
    """Прострочені `leased` → `pending` (attempt зберігається), lease очищено; `SKIP LOCKED`,
    щоб не чекати на jobs, які саме heartbeat-ять. Transaction boundary: викликач
    (maintenance/scheduler tick)."""
    current = resolve_now(now)
    expired = (
        select(CrawlJob.job_id)
        .where(CrawlJob.status == "leased", CrawlJob.lease_expires_at <= current)
        .order_by(CrawlJob.lease_expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(
        update(CrawlJob)
        .where(CrawlJob.job_id.in_(expired))
        .values(status="pending", lease_owner=None, lease_expires_at=None, updated_at=current)
        .returning(CrawlJob.job_id)
    )
    return list(result.scalars().all())


async def get_job(session: AsyncSession, job_id: UUID) -> CrawlJob | None:
    """Один job за PK. Transaction boundary: викликач; один SELECT, без блокування."""
    return await session.get(CrawlJob, job_id)


async def list_dead_letters(
    session: AsyncSession,
    *,
    after: tuple[datetime, UUID] | None = None,
    limit: int = 100,
    unresolved_only: bool = True,
) -> list[DeadLetter]:
    """Keyset pagination (§15) за `(created_at, id)`."""
    stmt = select(DeadLetter).order_by(DeadLetter.created_at, DeadLetter.id).limit(limit)
    if unresolved_only:
        stmt = stmt.where(DeadLetter.resolved_at.is_(None))
    if after is not None:
        created_at, dead_letter_id = after
        stmt = stmt.where(
            (DeadLetter.created_at > created_at)
            | ((DeadLetter.created_at == created_at) & (DeadLetter.id > dead_letter_id))
        )
    return list((await session.execute(stmt)).scalars().all())


def _owned(job_id: UUID, owner: str) -> ColumnElement[bool]:
    return (
        (CrawlJob.job_id == job_id)
        & (CrawlJob.status == "leased")
        & (CrawlJob.lease_owner == owner)
    )


def _not_owned_message(job_id: UUID, owner: str) -> str:
    return f"job {job_id}: lease не належить {owner!r} або job не в статусі leased"


async def _lock_owned(session: AsyncSession, job_id: UUID, owner: str) -> CrawlJob:
    job = await session.scalar(select(CrawlJob).where(_owned(job_id, owner)).with_for_update())
    if job is None:
        raise LeaseNotOwnedError(_not_owned_message(job_id, owner))
    return job


async def _lock_any(session: AsyncSession, job_id: UUID) -> CrawlJob:
    job = await session.get(CrawlJob, job_id, with_for_update=True)
    if job is None:
        msg = f"job {job_id} не знайдено"
        raise NotFoundError(msg)
    if job.status in TERMINAL_JOB_STATUSES:
        msg = f"job {job_id} уже термінальний ({job.status})"
        raise LeaseNotOwnedError(msg)
    return job


async def _quarantine_locked(
    session: AsyncSession,
    job: CrawlJob,
    *,
    reason: str,
    error_code: str,
    error_message: str | None,
    now: datetime,
) -> CrawlJob:
    job.status = "quarantined"
    job.lease_owner = None
    job.lease_expires_at = None
    job.finished_at = now
    job.last_error_code = error_code
    job.last_error_message = _truncate(error_message)
    job.updated_at = now
    await session.execute(
        insert(DeadLetter).values(
            id=new_entity_id(),
            job_id=job.job_id,
            job_type=job.job_type,
            reason=reason,
            attempt=job.attempt,
            error_code=error_code,
            error_message=_truncate(error_message),
            created_at=now,
        )
    )
    await session.flush()
    return job


def _truncate(message: str | None, limit: int = 2048) -> str | None:
    if message is None:
        return None
    return message if len(message) <= limit else message[: limit - 1] + "…"


__all__ = [
    "TERMINAL_JOB_STATUSES",
    "BackoffPolicy",
    "NewJob",
    "claim",
    "clamp_not_before",
    "complete",
    "enqueue",
    "get_job",
    "heartbeat",
    "list_dead_letters",
    "next_attempt_at",
    "quarantine",
    "recover_expired_leases",
    "release",
    "retry",
]
