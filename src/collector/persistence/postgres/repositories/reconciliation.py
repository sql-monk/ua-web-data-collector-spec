"""PostgreSQL-запити reconciler-а §7.3 крок 5 (PR3a п.9; spec-review PR2 SR-4).

Reconciler (WP-01B, `collector.workers.reconciler`) працює під `collector_projector` і лише
**читає** тут: SELECT на `projection_tasks`, `projection_acknowledgements`, `entity_index`,
`outbox_events` (GRANT — `sql/roles.sql`; UPDATE публікаційних колонок outbox у нього немає).
Виправлення робляться штатними операціями (`projection.acknowledge_projection` без `owner`,
reprojection), не звідси.

Операції:

- `list_stale_projection_tasks` — «незавершені tasks»: claimable (`pending`/`retry`) довше
  порогу з моменту `not_before` або `leased` довше порогу з моменту `leased_at`; keyset за
  `task_id`;
- `list_quarantined_projection_tasks` — quarantined tasks для звіту (reconciler їх не чіпає);
  keyset за `task_id`;
- `projection_completeness` — «повнота cursor»: відкриті й quarantined tasks джерела/сутності
  до watermark і найстаріші з них, плюс неопубліковані `domain.changed`.

Жодна операція не використовує `OFFSET` (§15). Transaction boundary усіх функцій — викликач;
лише SELECT без блокувань.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.models import (
    CLAIMABLE_PROJECTION_STATUSES,
    EntityIndex,
    OutboxEvent,
    ProjectionTask,
)

OPEN_TASK_STATUSES: tuple[str, ...] = (*CLAIMABLE_PROJECTION_STATUSES, "leased")
"""Task ще не acknowledged і не quarantined — reconciler має її звірити."""


async def list_stale_projection_tasks(
    session: AsyncSession,
    *,
    older_than: timedelta,
    after: UUID | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> list[ProjectionTask]:
    """Незавершені tasks, що «застрягли» довше `older_than` (`cutoff = now - older_than`):

    - `pending`/`retry` з `not_before <= cutoff` — claimable, але ніхто не взяв;
    - `leased` з `leased_at <= cutoff` — worker тримає lease (heartbeat-ить або помер до
      recover) довше порогу.

    Keyset за `task_id` (`after` — останній `task_id` попередньої сторінки).
    """
    if limit < 1:
        msg = "limit має бути >= 1"
        raise ValueError(msg)
    cutoff = resolve_now(now) - older_than
    stmt = (
        select(ProjectionTask)
        .where(
            or_(
                and_(
                    ProjectionTask.status.in_(CLAIMABLE_PROJECTION_STATUSES),
                    ProjectionTask.not_before <= cutoff,
                ),
                and_(ProjectionTask.status == "leased", ProjectionTask.leased_at <= cutoff),
            )
        )
        .order_by(ProjectionTask.task_id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(ProjectionTask.task_id > after)
    return list((await session.execute(stmt)).scalars().all())


async def list_quarantined_projection_tasks(
    session: AsyncSession,
    *,
    source_id: str | None = None,
    after: UUID | None = None,
    limit: int = 100,
) -> list[ProjectionTask]:
    """Quarantined tasks (за потреби — одного джерела, `entity_index.source_id`) для звіту
    reconciler-а; keyset за `task_id`."""
    if limit < 1:
        msg = "limit має бути >= 1"
        raise ValueError(msg)
    stmt = (
        select(ProjectionTask)
        .where(ProjectionTask.status == "quarantined")
        .order_by(ProjectionTask.task_id)
        .limit(limit)
    )
    if source_id is not None:
        stmt = stmt.join(EntityIndex, EntityIndex.entity_uuid == ProjectionTask.entity_uuid).where(
            EntityIndex.source_id == source_id
        )
    if after is not None:
        stmt = stmt.where(ProjectionTask.task_id > after)
    return list((await session.execute(stmt)).scalars().all())


@dataclass(frozen=True, slots=True)
class ProjectionCompleteness:
    """Стан «повноти cursor» для джерела/сутності до watermark."""

    open_tasks: int
    """`pending`/`retry`/`leased` — не acknowledged і не quarantined (лічильник drift)."""
    oldest_open_task_created_at: datetime | None
    quarantined_tasks: int
    """Не рахуються в drift, але блокують `complete` (картка WP-01A PR3a п.9)."""
    oldest_quarantined_task_created_at: datetime | None
    unpublished_domain_events: int
    """Неопубліковані (включно з припаркованими) `domain.changed` — сигнал доставки, не
    проєкції; на `complete`/`settled` не впливає."""
    oldest_unpublished_domain_event_created_at: datetime | None

    @property
    def settled(self) -> bool:
        """§7.3 крок 5 дослівно: кожна task або acknowledged, або quarantined."""
        return self.open_tasks == 0

    @property
    def complete(self) -> bool:
        """Картка PR3a п.9: cursor повністю опрацьований — немає ні відкритих, ні quarantined
        tasks (quarantined вимагає рішення оператора)."""
        return self.open_tasks == 0 and self.quarantined_tasks == 0


async def projection_completeness(
    session: AsyncSession,
    *,
    source_id: str | None = None,
    entity_uuid: UUID | None = None,
    created_before: datetime | None = None,
) -> ProjectionCompleteness:
    """«Повнота cursor» (§7.3 крок 5): агрегати по tasks, створених до `created_before`
    (watermark cursor-а; `None` — усі), для джерела (`entity_index.source_id`, canonical id)
    та/або сутності. Без фільтрів — по всій системі.

    `succeeded` task завжди має ack (`acknowledge_projection` ставить статус у тій самій
    транзакції), тому «не acknowledged» = статус не `succeeded`.
    """
    is_open = ProjectionTask.status.in_(OPEN_TASK_STATUSES)
    is_quarantined = ProjectionTask.status == "quarantined"
    tasks = select(
        func.count().filter(is_open),
        func.min(ProjectionTask.created_at).filter(is_open),
        func.count().filter(is_quarantined),
        func.min(ProjectionTask.created_at).filter(is_quarantined),
    ).where(ProjectionTask.status != "succeeded")
    events = select(func.count(), func.min(OutboxEvent.created_at)).where(
        OutboxEvent.topic == "domain", OutboxEvent.published_at.is_(None)
    )
    if source_id is not None:
        tasks = tasks.join(
            EntityIndex, EntityIndex.entity_uuid == ProjectionTask.entity_uuid
        ).where(EntityIndex.source_id == source_id)
        events = events.join(
            EntityIndex, EntityIndex.entity_uuid == OutboxEvent.aggregate_id
        ).where(EntityIndex.source_id == source_id)
    if entity_uuid is not None:
        tasks = tasks.where(ProjectionTask.entity_uuid == entity_uuid)
        events = events.where(OutboxEvent.aggregate_id == entity_uuid)
    if created_before is not None:
        tasks = tasks.where(ProjectionTask.created_at < created_before)
        events = events.where(OutboxEvent.created_at < created_before)
    open_count, oldest_open, quarantined_count, oldest_quarantined = (
        (await session.execute(tasks)).one().tuple()
    )
    unpublished, oldest_unpublished = (await session.execute(events)).one().tuple()
    return ProjectionCompleteness(
        open_tasks=int(open_count),
        oldest_open_task_created_at=oldest_open,
        quarantined_tasks=int(quarantined_count),
        oldest_quarantined_task_created_at=oldest_quarantined,
        unpublished_domain_events=int(unpublished),
        oldest_unpublished_domain_event_created_at=oldest_unpublished,
    )


__all__ = [
    "OPEN_TASK_STATUSES",
    "ProjectionCompleteness",
    "list_quarantined_projection_tasks",
    "list_stale_projection_tasks",
    "projection_completeness",
]
