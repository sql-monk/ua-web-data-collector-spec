"""Крок 2 і крок 4 потоку §7.3: видача projection version і фіксація Mongo receipt.

Операції: `record_parse_result`, `claim_projection_tasks`, `heartbeat_projection_task`,
`retry_projection_task`, `release_projection_task`, `recover_expired_projection_leases`,
`quarantine_projection_task`, `acknowledge_projection`, `get_projection_task`.

Дві транзакційні межі, які визначають коректність усього потоку:

1. **`record_parse_result` — одна транзакція** (§7.3 п.2): `parse_attempts` +
   `normalized_artifacts` + атомарна видача монотонної `projection_version` +
   `projection_tasks` + `outbox_events(projection.command)`. Версія видається під row lock на
   рядку `entity_index` (`SELECT … FOR UPDATE`), тому конкурентні виклики для однієї сутності
   отримують `1, 2, 3` без дірок і без дублів. Дірок немає саме тому, що lock тримається до
   commit: якщо транзакція відкотиться, лічильник відкотиться разом із нею (послідовність
   `SEQUENCE` дала б дірку).

2. **`acknowledge_projection` — одна транзакція** (§7.3 п.4, §9.5): `projection_acknowledgements`
   (PK `task_id` → повторний ack ідемпотентний) + монотонний `GREATEST` для
   `confirmed_projection_version` + `projection_tasks.status = 'succeeded'` + — лише для
   `applied_to_current AND state_changed` — `change_events` і `outbox_events(domain.changed)`
   з **тими самими bytes**, що в receipt (`bytea`, без повторної серіалізації; R-37/R-42).

Порядок доставки не має значення: старіша task записує свій exact-version ack, але
`GREATEST` не дає їй знизити confirmed version (§9.5).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import (
    AppliedProjectionReceipt,
    DomainChangedEvent,
    NormalizedArtifactRef,
    ProjectionCommand,
    canonical_json_bytes,
    decode_event,
    new_entity_id,
    sha256_hex,
    should_emit_domain_changed,
)
from collector.contracts.canonical import CANONICAL_JSON_MEDIA_TYPE
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import (
    ConflictError,
    LeaseNotOwnedError,
    NotFoundError,
)
from collector.persistence.postgres.models import (
    CLAIMABLE_PROJECTION_STATUSES,
    PROJECTION_COMMAND_EVENT_TYPE,
    ChangeEvent,
    EntityIndex,
    NormalizedArtifact,
    OutboxEvent,
    ParseAttempt,
    ProjectionAcknowledgement,
    ProjectionTask,
)
from collector.persistence.postgres.repositories.artifacts import normalized_artifact_values
from collector.persistence.postgres.repositories.queue import BackoffPolicy

TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({"succeeded", "quarantined"})


@dataclass(frozen=True, slots=True)
class ParseAttemptRecord:
    """Рядок `parse_attempts` (§9.1): що саме парсер зробив із raw object."""

    raw_sha256: str
    parser_version: str
    outcome: str
    domain: str
    fetch_id: UUID | None = None
    job_id: UUID | None = None
    source_id: UUID | None = None
    records_count: int = 0
    validation_errors_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Що саме `record_parse_result` записує в одній транзакції."""

    parse_attempt: ParseAttempt
    artifact: NormalizedArtifact
    task: ProjectionTask
    command: ProjectionCommand
    outbox_event: OutboxEvent
    created: bool
    """`False` — повторний виклик для того самого artifact: повернено наявні task/command."""


async def record_parse_result(
    session: AsyncSession,
    *,
    attempt: ParseAttemptRecord,
    artifact_ref: NormalizedArtifactRef,
    object_key: str,
    target_collection: str,
    target_schema_version: str,
    priority: int = 0,
    max_attempts: int = 5,
    not_before: datetime | None = None,
    now: datetime | None = None,
) -> ParseResult:
    """§7.3 крок 2 / §10 п.8 — **одна транзакція**, викликач лише робить commit.

    Послідовність:

    1. row lock на `entity_index` сутності (`SELECT … FOR UPDATE`) — серіалізує видачу версій
       для цієї сутності і нікого більше; рядок має вже існувати
       (`entities.upsert_entity` — identity resolution передує парсингу, §9.3);
    2. `normalized_artifacts` `ON CONFLICT (object_key) DO NOTHING`. Конфлікт означає
       **повторний виклик для того самого artifact**: функція повертає наявний task і
       `created=False`, не витрачаючи нову `projection_version` і не пишучи ні другого
       `parse_attempt`, ні другого outbox-рядка (ключ ідемпотентності — content-addressed
       `object_key`);
    3. `parse_attempts`;
    4. `entity_index.projection_version + 1` → `projection_tasks`;
    5. `outbox_events` з canonical bytes `ProjectionCommand` (топік `internal`: R-30 —
       команда не публікується зовнішнім споживачам; `event_id = task_id` робить її unique).

    `artifact_ref.entity_uuid` — джерело істини для сутності; `ConflictError`, якщо він не
    збігається з `entity_uuid` наявного artifact.
    """
    current = resolve_now(now)
    entity = await session.scalar(
        select(EntityIndex)
        .where(EntityIndex.entity_uuid == artifact_ref.entity_uuid)
        .with_for_update()
    )
    if entity is None:
        msg = (
            f"entity {artifact_ref.entity_uuid} не знайдено в entity_index — спершу "
            "entities.upsert_entity (identity resolution передує парсингу, §9.3)"
        )
        raise NotFoundError(msg)

    values = normalized_artifact_values(artifact_ref, object_key=object_key, now=current)
    values["parse_attempt_id"] = None
    inserted = (
        await session.execute(
            pg_insert(NormalizedArtifact)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[NormalizedArtifact.object_key])
            .returning(NormalizedArtifact)
        )
    ).scalar_one_or_none()
    if inserted is None:
        return await _existing_parse_result(session, object_key, target_collection, artifact_ref)

    parse_attempt = ParseAttempt(
        parse_attempt_id=new_entity_id(),
        fetch_id=attempt.fetch_id,
        job_id=attempt.job_id,
        source_id=attempt.source_id,
        raw_sha256=attempt.raw_sha256,
        domain=attempt.domain,
        parser_version=attempt.parser_version,
        outcome=attempt.outcome,
        records_count=attempt.records_count,
        validation_errors_count=attempt.validation_errors_count,
        error_code=attempt.error_code,
        error_message=attempt.error_message,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at or current,
        created_at=current,
    )
    session.add(parse_attempt)
    await session.flush()
    inserted.parse_attempt_id = parse_attempt.parse_attempt_id

    version = entity.projection_version + 1
    entity.projection_version = version
    entity.updated_at = current

    task = ProjectionTask(
        task_id=new_entity_id(),
        artifact_id=inserted.artifact_id,
        entity_uuid=entity.entity_uuid,
        projection_version=version,
        target_collection=target_collection,
        target_schema_version=target_schema_version,
        status="pending",
        priority=priority,
        attempt=0,
        max_attempts=max_attempts,
        not_before=not_before or current,
        created_at=current,
        updated_at=current,
    )
    session.add(task)
    await session.flush()

    command = ProjectionCommand(
        task_id=task.task_id,
        entity_uuid=entity.entity_uuid,
        projection_version=version,
        target_collection=target_collection,
        target_schema_version=target_schema_version,
        artifact=artifact_ref,
        priority=priority,
        not_before=task.not_before,
        issued_at=current,
    )
    outbox_event = _command_outbox_row(command, now=current)
    session.add(outbox_event)
    await session.flush()
    return ParseResult(
        parse_attempt=parse_attempt,
        artifact=inserted,
        task=task,
        command=command,
        outbox_event=outbox_event,
        created=True,
    )


async def claim_projection_tasks(
    session: AsyncSession,
    worker: str,
    lease_seconds: int,
    *,
    target_collections: Sequence[str] | None = None,
    limit: int = 1,
    now: datetime | None = None,
) -> list[ProjectionTask]:
    """`FOR UPDATE SKIP LOCKED` claim із тією самою семантикою, що й `queue.claim` (§7.2).

    Transaction boundary: викликач, commit одразу після виклику — до commit тримаються row
    locks на вибраних tasks.
    """
    if limit < 1 or lease_seconds < 1:
        msg = "limit і lease_seconds мають бути >= 1"
        raise ValueError(msg)
    current = resolve_now(now)
    candidates = (
        select(ProjectionTask.task_id)
        .where(
            ProjectionTask.status.in_(CLAIMABLE_PROJECTION_STATUSES),
            ProjectionTask.not_before <= current,
        )
        .order_by(ProjectionTask.priority.desc(), ProjectionTask.not_before, ProjectionTask.task_id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if target_collections:
        candidates = candidates.where(
            ProjectionTask.target_collection.in_(list(target_collections))
        )
    claimed = list(
        (
            await session.execute(
                update(ProjectionTask)
                .where(ProjectionTask.task_id.in_(candidates))
                .values(
                    status="leased",
                    lease_owner=worker,
                    lease_expires_at=current + timedelta(seconds=lease_seconds),
                    leased_at=current,
                    attempt=ProjectionTask.attempt + 1,
                    updated_at=current,
                )
                .returning(ProjectionTask)
            )
        )
        .scalars()
        .all()
    )
    claimed.sort(key=lambda task: (-task.priority, task.not_before, task.task_id))
    return claimed


async def heartbeat_projection_task(
    session: AsyncSession,
    task_id: UUID,
    owner: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Продовжує lease власнику; чужий/відсутній lease → `LeaseNotOwnedError`."""
    current = resolve_now(now)
    expires = current + timedelta(seconds=lease_seconds)
    updated = await session.scalar(
        update(ProjectionTask)
        .where(_owned(task_id, owner))
        .values(lease_expires_at=expires, updated_at=current)
        .returning(ProjectionTask.task_id)
    )
    if updated is None:
        raise LeaseNotOwnedError(_not_owned_message(task_id, owner))
    return expires


async def retry_projection_task(
    session: AsyncSession,
    task_id: UUID,
    owner: str,
    *,
    error_code: str,
    error_message: str | None = None,
    policy: BackoffPolicy | None = None,
    now: datetime | None = None,
) -> ProjectionTask:
    """Retryable-помилка projector-а: `leased` → `retry` з backoff; після `max_attempts` —
    `quarantined` (reconciler §7.3 п.5 розбирає такі tasks окремо)."""
    current = resolve_now(now)
    task = await _lock_owned(session, task_id, owner)
    if task.attempt >= task.max_attempts:
        return _quarantine_locked(task, error_code=error_code, error_message=error_message,
                                  now=current)
    delay = (policy or BackoffPolicy()).delay_for(task.attempt, random.SystemRandom())
    task.status = "retry"
    task.lease_owner = None
    task.lease_expires_at = None
    task.not_before = current + delay
    task.last_error_code = error_code
    task.last_error_message = _truncate(error_message)
    task.updated_at = current
    await session.flush()
    return task


async def release_projection_task(
    session: AsyncSession, task_id: UUID, owner: str, *, now: datetime | None = None
) -> ProjectionTask:
    """Плановий drain projector-а: `leased` → `pending` без інкременту `attempt` і без
    помилки (та сама семантика, що `queue.release`)."""
    current = resolve_now(now)
    task = await session.scalar(
        update(ProjectionTask)
        .where(_owned(task_id, owner))
        .values(
            status="pending",
            lease_owner=None,
            lease_expires_at=None,
            leased_at=None,
            not_before=current,
            updated_at=current,
        )
        .returning(ProjectionTask)
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise LeaseNotOwnedError(_not_owned_message(task_id, owner))
    return task


async def quarantine_projection_task(
    session: AsyncSession,
    task_id: UUID,
    *,
    error_code: str,
    error_message: str | None = None,
    now: datetime | None = None,
) -> ProjectionTask:
    """Операторський/permanent-failure перехід у `quarantined` (без перевірки lease)."""
    current = resolve_now(now)
    task = await session.get(ProjectionTask, task_id, with_for_update=True)
    if task is None:
        msg = f"projection task {task_id} не знайдено"
        raise NotFoundError(msg)
    if task.status in TERMINAL_TASK_STATUSES:
        msg = f"projection task {task_id} уже термінальна ({task.status})"
        raise LeaseNotOwnedError(msg)
    result = _quarantine_locked(task, error_code=error_code, error_message=error_message,
                                now=current)
    await session.flush()
    return result


async def recover_expired_projection_leases(
    session: AsyncSession, *, limit: int = 1000, now: datetime | None = None
) -> list[UUID]:
    """Прострочені `leased` → `pending` (attempt зберігається), lease очищено."""
    current = resolve_now(now)
    expired = (
        select(ProjectionTask.task_id)
        .where(ProjectionTask.status == "leased", ProjectionTask.lease_expires_at <= current)
        .order_by(ProjectionTask.lease_expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(
        update(ProjectionTask)
        .where(ProjectionTask.task_id.in_(expired))
        .values(status="pending", lease_owner=None, lease_expires_at=None, updated_at=current)
        .returning(ProjectionTask.task_id)
    )
    return list(result.scalars().all())


@dataclass(frozen=True, slots=True)
class AcknowledgeResult:
    """Що дав ack: сам запис, підтверджена версія і (за наявності) створена подія."""

    acknowledgement: ProjectionAcknowledgement
    confirmed_projection_version: int
    change_event: ChangeEvent | None
    outbox_event: OutboxEvent | None
    created: bool
    """`False` — ack для цього `task_id` уже існував; нічого не змінено (ідемпотентний повтор)."""


async def acknowledge_projection(
    session: AsyncSession,
    task_id: UUID,
    receipt: AppliedProjectionReceipt,
    *,
    event: DomainChangedEvent | None = None,
    now: datetime | None = None,
) -> AcknowledgeResult:
    """§7.3 крок 4 / §9.5 — **одна транзакція**, викликач лише робить commit.

    Послідовність:

    1. `projection_acknowledgements` `ON CONFLICT (task_id) DO NOTHING`. Конфлікт означає
       повторний ack (crash між Mongo commit і PostgreSQL commit, replay reconciler-а):
       повертається наявний запис, `created=False`, і **жодного** іншого рядка не додається —
       саме тому повтор не створює другої події;
    2. `entity_index.confirmed_projection_version = GREATEST(existing, receipt.projection_version)`
       — out-of-order доставка (3, 1, 2) ніколи не знижує підтверджену версію (§9.5);
    3. task → `succeeded`;
    4. лише якщо `applied_to_current AND state_changed` (`should_emit_domain_changed`) —
       `change_events` + `outbox_events(topic='domain')` з bytes/hash **із receipt**, без
       повторної серіалізації: після crash replay дає byte-equivalent подію (R-37/R-42).

    `event` потрібен лише тоді, коли подія завелика для inline (`receipt.event_artifact`): з
    неї беруться `event_type`/`payload_schema_version`, яких немає в receipt. Для звичайного
    inline-випадку метадані декодуються з самих bytes (`decode_event`) — bytes при цьому
    зберігаються як є.
    """
    current = resolve_now(now)
    if receipt.projection_task_id != task_id:
        msg = (
            f"receipt належить task {receipt.projection_task_id}, а ack робиться для {task_id}"
        )
        raise ConflictError(msg)
    task = await session.get(ProjectionTask, task_id, with_for_update=True)
    if task is None:
        msg = f"projection task {task_id} не знайдено"
        raise NotFoundError(msg)
    if task.entity_uuid != receipt.entity_uuid or task.projection_version != receipt.projection_version:
        msg = (
            f"receipt не відповідає task {task_id}: entity/version "
            f"({receipt.entity_uuid}, {receipt.projection_version}) vs "
            f"({task.entity_uuid}, {task.projection_version})"
        )
        raise ConflictError(msg)

    inserted = (
        await session.execute(
            pg_insert(ProjectionAcknowledgement)
            .values(
                task_id=task_id,
                entity_uuid=receipt.entity_uuid,
                projection_version=receipt.projection_version,
                receipt_id=receipt.projection_task_id,
                receipt_cluster_time=receipt.cluster_time,
                mongo_document_id=receipt.document_id,
                applied_to_current=receipt.applied_to_current,
                state_changed=receipt.state_changed,
                result_version=receipt.result_version,
                result_hash=receipt.result_hash,
                previous_hash=receipt.previous_hash,
                event_id=receipt.event_id,
                event_sha256=_receipt_event_sha256(receipt),
                acknowledged_at=current,
                created_at=current,
            )
            .on_conflict_do_nothing(index_elements=[ProjectionAcknowledgement.task_id])
            .returning(ProjectionAcknowledgement)
        )
    ).scalar_one_or_none()
    if inserted is None:
        existing = await session.get(
            ProjectionAcknowledgement, task_id, populate_existing=True
        )
        if existing is None:  # pragma: no cover — можливо лише поза READ COMMITTED
            msg = f"ack {task_id} зник між INSERT і SELECT (потрібен READ COMMITTED)"
            raise NotFoundError(msg)
        confirmed = await _confirmed_version(session, receipt.entity_uuid)
        return AcknowledgeResult(
            acknowledgement=existing,
            confirmed_projection_version=confirmed,
            change_event=None,
            outbox_event=None,
            created=False,
        )

    confirmed = await session.scalar(
        update(EntityIndex)
        .where(EntityIndex.entity_uuid == receipt.entity_uuid)
        .values(
            confirmed_projection_version=func.greatest(
                EntityIndex.confirmed_projection_version, receipt.projection_version
            ),
            confirmed_at=current,
            mongo_collection=func.coalesce(EntityIndex.mongo_collection, receipt.target_collection),
            mongo_document_id=func.coalesce(EntityIndex.mongo_document_id, receipt.document_id),
            updated_at=current,
        )
        .returning(EntityIndex.confirmed_projection_version)
    )
    if confirmed is None:
        msg = f"entity {receipt.entity_uuid} не знайдено в entity_index"
        raise NotFoundError(msg)

    task.status = "succeeded"
    task.lease_owner = None
    task.lease_expires_at = None
    task.finished_at = current
    task.updated_at = current

    change_event: ChangeEvent | None = None
    outbox_event: OutboxEvent | None = None
    if should_emit_domain_changed(receipt):
        change_event, outbox_event = _domain_changed_rows(receipt, event=event, now=current)
        session.add(change_event)
        session.add(outbox_event)
    await session.flush()
    return AcknowledgeResult(
        acknowledgement=inserted,
        confirmed_projection_version=int(confirmed),
        change_event=change_event,
        outbox_event=outbox_event,
        created=True,
    )


async def get_projection_task(session: AsyncSession, task_id: UUID) -> ProjectionTask | None:
    """Одна task за PK. Transaction boundary: викликач; один SELECT без блокування."""
    return await session.get(ProjectionTask, task_id)


async def get_acknowledgement(
    session: AsyncSession, task_id: UUID
) -> ProjectionAcknowledgement | None:
    """Ack за PK `task_id`."""
    return await session.get(ProjectionAcknowledgement, task_id)


# --- внутрішні ---------------------------------------------------------------------------


def _command_outbox_row(command: ProjectionCommand, *, now: datetime) -> OutboxEvent:
    payload = canonical_json_bytes(command)
    return OutboxEvent(
        outbox_id=new_entity_id(),
        # `task_id` unique за PK `projection_tasks`, тому unique(event_id) виконується без
        # окремого генератора id: одна task — рівно одна команда.
        event_id=command.task_id,
        topic="internal",
        event_type=PROJECTION_COMMAND_EVENT_TYPE,
        aggregate_id=command.entity_uuid,
        aggregate_version=command.projection_version,
        payload_schema_version=command.schema_version,
        payload_bytes=payload,
        payload_media_type=CANONICAL_JSON_MEDIA_TYPE,
        payload_sha256=sha256_hex(payload),
        available_at=command.not_before or now,
        created_at=now,
        updated_at=now,
    )


def _receipt_event_sha256(receipt: AppliedProjectionReceipt) -> str | None:
    if receipt.event_sha256 is not None:
        return receipt.event_sha256
    if receipt.event_artifact is not None:
        return receipt.event_artifact.sha256
    return None


def _domain_changed_rows(
    receipt: AppliedProjectionReceipt,
    *,
    event: DomainChangedEvent | None,
    now: datetime,
) -> tuple[ChangeEvent, OutboxEvent]:
    """Рядки `change_events`/`outbox_events` з готових bytes receipt (R-42)."""
    descriptor = event
    if descriptor is None and receipt.event_bytes is not None:
        descriptor = decode_event(receipt.event_bytes)
    if descriptor is None:
        msg = (
            "receipt несе подію як artifact — передайте `event=DomainChangedEvent`, бо "
            "event_type/payload_schema_version у receipt не зберігаються"
        )
        raise ConflictError(msg)
    if receipt.event_id is not None and descriptor.event_id != receipt.event_id:
        msg = f"event_id події {descriptor.event_id} не збігається з receipt {receipt.event_id}"
        raise ConflictError(msg)
    event_sha256 = _receipt_event_sha256(receipt)
    if event_sha256 is None:  # pragma: no cover — заборонено валідатором контракту
        msg = "receipt із domain.changed має нести event_sha256 або event_artifact"
        raise ConflictError(msg)
    media_type = receipt.event_media_type or (
        receipt.event_artifact.media_type if receipt.event_artifact is not None else None
    )
    if media_type is None:  # pragma: no cover — заборонено валідатором контракту
        msg = "receipt із domain.changed має нести event_media_type"
        raise ConflictError(msg)
    artifact_uri = receipt.event_artifact.uri if receipt.event_artifact is not None else None
    artifact_size = (
        receipt.event_artifact.size_bytes if receipt.event_artifact is not None else None
    )
    change_event = ChangeEvent(
        change_event_id=new_entity_id(),
        event_id=descriptor.event_id,
        aggregate_id=receipt.entity_uuid,
        aggregate_version=receipt.projection_version,
        event_type=descriptor.event_type,
        payload_schema_version=descriptor.payload_schema_version,
        projection_task_id=receipt.projection_task_id,
        previous_state_hash=receipt.previous_hash,
        result_state_hash=receipt.result_hash,
        event_bytes=receipt.event_bytes,
        event_media_type=media_type,
        event_sha256=event_sha256,
        event_artifact_uri=artifact_uri,
        event_artifact_size_bytes=artifact_size,
        occurred_at=receipt.committed_at,
        created_at=now,
    )
    outbox_event = OutboxEvent(
        outbox_id=new_entity_id(),
        event_id=descriptor.event_id,
        topic="domain",
        event_type=descriptor.event_type,
        aggregate_id=receipt.entity_uuid,
        aggregate_version=receipt.projection_version,
        payload_schema_version=descriptor.payload_schema_version,
        payload_bytes=receipt.event_bytes,
        payload_media_type=media_type,
        payload_sha256=event_sha256,
        payload_artifact_uri=artifact_uri,
        available_at=now,
        created_at=now,
        updated_at=now,
    )
    return change_event, outbox_event


async def _existing_parse_result(
    session: AsyncSession,
    object_key: str,
    target_collection: str,
    artifact_ref: NormalizedArtifactRef,
) -> ParseResult:
    artifact = await session.scalar(
        select(NormalizedArtifact)
        .where(NormalizedArtifact.object_key == object_key)
        .execution_options(populate_existing=True)
    )
    if artifact is None:  # pragma: no cover — можливо лише поза READ COMMITTED
        msg = f"normalized artifact {object_key!r} зник між INSERT і SELECT"
        raise NotFoundError(msg)
    if artifact.entity_uuid != artifact_ref.entity_uuid:
        msg = (
            f"object_key {object_key!r} уже належить сутності {artifact.entity_uuid}, "
            f"а не {artifact_ref.entity_uuid}"
        )
        raise ConflictError(msg)
    task = await session.scalar(
        select(ProjectionTask).where(
            ProjectionTask.artifact_id == artifact.artifact_id,
            ProjectionTask.target_collection == target_collection,
        )
    )
    if task is None:
        msg = (
            f"artifact {object_key!r} уже є, але task для {target_collection!r} немає — "
            "часткова транзакція неможлива, перевірте ручні втручання у схему"
        )
        raise ConflictError(msg)
    outbox_event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.event_id == task.task_id)
    )
    if outbox_event is None:  # pragma: no cover — пишеться в тій самій транзакції, що й task
        msg = f"outbox-рядок команди для task {task.task_id} відсутній"
        raise ConflictError(msg)
    parse_attempt = (
        await session.get(ParseAttempt, artifact.parse_attempt_id)
        if artifact.parse_attempt_id is not None
        else None
    )
    if parse_attempt is None:  # pragma: no cover — пишеться в тій самій транзакції
        msg = f"parse_attempt артефакту {object_key!r} відсутній"
        raise ConflictError(msg)
    command = ProjectionCommand(
        task_id=task.task_id,
        entity_uuid=task.entity_uuid,
        projection_version=task.projection_version,
        target_collection=task.target_collection,
        target_schema_version=task.target_schema_version,
        artifact=artifact_ref,
        priority=task.priority,
        not_before=task.not_before,
        issued_at=task.created_at,
    )
    return ParseResult(
        parse_attempt=parse_attempt,
        artifact=artifact,
        task=task,
        command=command,
        outbox_event=outbox_event,
        created=False,
    )


async def _confirmed_version(session: AsyncSession, entity_uuid: UUID) -> int:
    version = await session.scalar(
        select(EntityIndex.confirmed_projection_version).where(
            EntityIndex.entity_uuid == entity_uuid
        )
    )
    if version is None:  # pragma: no cover — FK гарантує наявність
        msg = f"entity {entity_uuid} не знайдено в entity_index"
        raise NotFoundError(msg)
    return int(version)


def _owned(task_id: UUID, owner: str) -> ColumnElement[bool]:
    return (
        (ProjectionTask.task_id == task_id)
        & (ProjectionTask.status == "leased")
        & (ProjectionTask.lease_owner == owner)
    )


def _not_owned_message(task_id: UUID, owner: str) -> str:
    return f"projection task {task_id}: lease не належить {owner!r} або task не в статусі leased"


async def _lock_owned(session: AsyncSession, task_id: UUID, owner: str) -> ProjectionTask:
    task = await session.scalar(
        select(ProjectionTask).where(_owned(task_id, owner)).with_for_update()
    )
    if task is None:
        raise LeaseNotOwnedError(_not_owned_message(task_id, owner))
    return task


def _quarantine_locked(
    task: ProjectionTask, *, error_code: str, error_message: str | None, now: datetime
) -> ProjectionTask:
    task.status = "quarantined"
    task.lease_owner = None
    task.lease_expires_at = None
    task.finished_at = now
    task.last_error_code = error_code
    task.last_error_message = _truncate(error_message)
    task.updated_at = now
    return task


def _truncate(message: str | None, limit: int = 2048) -> str | None:
    if message is None:
        return None
    return message if len(message) <= limit else message[: limit - 1] + "…"


__all__ = [
    "TERMINAL_TASK_STATUSES",
    "AcknowledgeResult",
    "ParseAttemptRecord",
    "ParseResult",
    "acknowledge_projection",
    "claim_projection_tasks",
    "get_acknowledgement",
    "get_projection_task",
    "heartbeat_projection_task",
    "quarantine_projection_task",
    "recover_expired_projection_leases",
    "record_parse_result",
    "release_projection_task",
    "retry_projection_task",
]
