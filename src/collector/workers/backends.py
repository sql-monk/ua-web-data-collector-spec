"""Черги, з якими працює `WorkerRuntime`: `crawl_jobs` і `projection_tasks` (PR1c п.5).

Runtime не знає, у якій таблиці лежить task: claim, heartbeat і звіт він робить через
`QueueBackend`, а конкретну таблицю обирає прив'язка ролі (`HandlerBinding`). Обидві реалізації
— тонкі адаптери над репозиторіями WP-01A (`repositories/queue.py`, `repositories/projection.py`):
жодного SQL тут немає, транзакцію відкриває runtime (`bounded_transaction`), а backend лише
виконує свої кроки всередині неї.

Семантика однакова для обох черг:

- `claim` — `FOR UPDATE SKIP LOCKED`, інкремент `attempt`, lease на `owner`;
- `heartbeat`/`complete`/`retry`/`defer`/`quarantine`/`release` — лише власником lease, інакше
  `LeaseNotOwnedError` (runtime трактує як `lease_lost`);
- `defer` і `release` **не** спалюють спробу (компенсують інкремент claim), не пишуть полів
  помилки й dead letter; `defer` додатково ставить `not_before = until`;
- `retry` без `not_before` — дефолтний `BackoffPolicy` черги (поведінка до PR1c); з
  `not_before` — рівно цей момент (runtime уже обчислив `max(now + розклад, нижня межа)`).

**Ack у report-транзакції** (`ProjectionTasksBackend.complete`): `acknowledge_projection` з
`owner` виконується в тій самій транзакції, що й звіт runtime-у — ack, `GREATEST` confirmed
version, task `succeeded` і `domain.changed` з bytes receipt комітяться разом або ніяк.

Залежність від WP-01A PR3a: `not_before` у `queue.retry`/`queue.release`/
`retry_projection_task`/`release_projection_task` і `owner` в `acknowledge_projection`
(картка WP-01A, PR3a п.1, п.7; merged у PR #12).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from collector.contracts.events import DomainChangedEvent
from collector.contracts.projection import AppliedProjectionReceipt
from collector.persistence.postgres.repositories import projection as projection_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.handlers import Task

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from collector.persistence.postgres.models import CrawlJob, ProjectionTask

IMMEDIATE_POLICY = queue_repo.BackoffPolicy(base=timedelta(0), jitter_ratio=0.0)
"""Нульовий backoff: `not_before` уже обчислив runtime — черга бере його без власної затримки.

Разом із `not_before=` дає той самий результат за обох семантик, дозволених карткою PR3a п.1
(«`max(now + backoff, not_before)`» або «саме `not_before`»)."""


class InvalidHandlerOutputError(ValueError):
    """Output не підходить черзі: runtime карантинить task (`invalid_handler_output`)."""


@dataclass(frozen=True, slots=True)
class ProjectionAck:
    """Output projector-а, коли подія `domain.changed` лежить artifact-ом (> 256 KiB).

    `acknowledge_projection` не може відновити `event_type`/`payload_schema_version` з receipt,
    у якому є лише `event_artifact`, — тоді handler повертає receipt разом із дескриптором події.
    Для receipt з inline `event_bytes` (або без події) достатньо самого `AppliedProjectionReceipt`.
    """

    receipt: AppliedProjectionReceipt
    event: DomainChangedEvent | None = None


class QueueBackend(Protocol):
    """Черга tasks для runtime; усі методи працюють у транзакції викликача."""

    @property
    def name(self) -> str:
        """Ім'я черги для логів (`crawl_jobs`, `projection_tasks`)."""
        ...

    def check_output(self, output: object) -> None:
        """`InvalidHandlerOutputError`, якщо `output` успішного результату не підходить черзі."""
        ...

    async def claim(
        self,
        session: AsyncSession,
        job_types: Sequence[str],
        owner: str,
        lease_seconds: int,
        *,
        limit: int,
        now: datetime,
    ) -> list[Task]: ...

    async def heartbeat(
        self, session: AsyncSession, job_id: UUID, owner: str, lease_seconds: int, *, now: datetime
    ) -> None: ...

    async def complete(
        self, session: AsyncSession, task: Task, owner: str, output: object, *, now: datetime
    ) -> None: ...

    async def retry(
        self,
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        not_before: datetime | None,
        now: datetime,
    ) -> str:
        """Повертає підсумковий статус (`retry` або `quarantined` після `max_attempts`)."""
        ...

    async def defer(
        self, session: AsyncSession, job_id: UUID, owner: str, *, until: datetime, now: datetime
    ) -> None: ...

    async def quarantine(
        self,
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        now: datetime,
    ) -> None: ...

    async def release(
        self, session: AsyncSession, job_id: UUID, owner: str, *, now: datetime
    ) -> None: ...

    async def recover_expired(
        self, session: AsyncSession, *, limit: int, now: datetime
    ) -> list[UUID]: ...


# --- crawl_jobs -----------------------------------------------------------------------------


def task_from_job(job: CrawlJob) -> Task:
    """Знімок `crawl_jobs` для handler-а (без ORM-об'єкта)."""
    return Task(
        job_id=job.job_id,
        job_type=job.job_type,
        args=dict(job.args),
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        priority=job.priority,
        not_before=job.not_before,
        run_id=job.run_id,
        source_id=job.source_id,
    )


class CrawlJobsBackend:
    """`crawl_jobs` через `repositories/queue.py` — поведінка runtime до PR1c."""

    @property
    def name(self) -> str:
        return "crawl_jobs"

    def check_output(self, output: object) -> None:
        if output is not None:
            # У `crawl_jobs` немає куди записати output — мовчки викинути його гірше, ніж
            # зупинити task: handler очевидно розраховував, що результат кудись піде.
            msg = f"crawl_jobs не приймає output успішного результату ({type(output).__name__})"
            raise InvalidHandlerOutputError(msg)

    async def claim(
        self,
        session: AsyncSession,
        job_types: Sequence[str],
        owner: str,
        lease_seconds: int,
        *,
        limit: int,
        now: datetime,
    ) -> list[Task]:
        jobs = await queue_repo.claim(
            session, job_types, owner, lease_seconds, limit=limit, now=now
        )
        return [task_from_job(job) for job in jobs]

    async def heartbeat(
        self, session: AsyncSession, job_id: UUID, owner: str, lease_seconds: int, *, now: datetime
    ) -> None:
        await queue_repo.heartbeat(session, job_id, owner, lease_seconds, now=now)

    async def complete(
        self, session: AsyncSession, task: Task, owner: str, output: object, *, now: datetime
    ) -> None:
        self.check_output(output)
        await queue_repo.complete(session, task.job_id, owner, now=now)

    async def retry(
        self,
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        not_before: datetime | None,
        now: datetime,
    ) -> str:
        if not_before is None:
            job = await queue_repo.retry(
                session, job_id, owner, error_code=error_code, error_message=error_message, now=now
            )
        else:
            job = await queue_repo.retry(
                session,
                job_id,
                owner,
                error_code=error_code,
                error_message=error_message,
                policy=IMMEDIATE_POLICY,
                not_before=not_before,
                now=now,
            )
        return job.status

    async def defer(
        self, session: AsyncSession, job_id: UUID, owner: str, *, until: datetime, now: datetime
    ) -> None:
        # Саме `release`, а не `retry`: retry спалив би спробу, записав би помилку і на
        # останній спробі відправив би job у карантин (PR1c п.1).
        await queue_repo.release(session, job_id, owner, not_before=until, now=now)

    async def quarantine(
        self,
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        now: datetime,
    ) -> None:
        await queue_repo.quarantine(
            session, job_id, owner, error_code=error_code, error_message=error_message, now=now
        )

    async def release(
        self, session: AsyncSession, job_id: UUID, owner: str, *, now: datetime
    ) -> None:
        await queue_repo.release(session, job_id, owner, now=now)

    async def recover_expired(
        self, session: AsyncSession, *, limit: int, now: datetime
    ) -> list[UUID]:
        return await queue_repo.recover_expired_leases(session, limit=limit, now=now)


# --- projection_tasks -----------------------------------------------------------------------


def task_from_projection(task: ProjectionTask) -> Task:
    """Знімок `projection_tasks` для handler-а.

    `job_id = task_id`, `job_type = target_collection`; `args` — поля `ProjectionCommand`, які
    є в рядку task (сутність, версія, цільова collection/schema, `artifact_id` і `parse_key`).
    Повний `NormalizedArtifactRef` handler читає сам за `artifact_id` (роль `collector_projector`
    має SELECT на `normalized_artifacts`).
    """
    return Task(
        job_id=task.task_id,
        job_type=task.target_collection,
        args={
            "task_id": task.task_id,
            "entity_uuid": task.entity_uuid,
            "projection_version": task.projection_version,
            "target_collection": task.target_collection,
            "target_schema_version": task.target_schema_version,
            "artifact_id": task.artifact_id,
            "parse_key": task.parse_key,
        },
        attempt=task.attempt,
        max_attempts=task.max_attempts,
        priority=task.priority,
        not_before=task.not_before,
        run_id=None,
        source_id=None,
    )


def _projection_output(output: object) -> ProjectionAck:
    if isinstance(output, ProjectionAck):
        return output
    if isinstance(output, AppliedProjectionReceipt):
        return ProjectionAck(receipt=output)
    msg = (
        "projection_tasks чекає output = AppliedProjectionReceipt або ProjectionAck, отримано "
        f"{type(output).__name__}"
    )
    raise InvalidHandlerOutputError(msg)


class ProjectionTasksBackend:
    """`projection_tasks` через `repositories/projection.py`; `job_types` = target collections."""

    @property
    def name(self) -> str:
        return "projection_tasks"

    def check_output(self, output: object) -> None:
        _projection_output(output)

    async def claim(
        self,
        session: AsyncSession,
        job_types: Sequence[str],
        owner: str,
        lease_seconds: int,
        *,
        limit: int,
        now: datetime,
    ) -> list[Task]:
        tasks = await projection_repo.claim_projection_tasks(
            session, owner, lease_seconds, target_collections=job_types, limit=limit, now=now
        )
        return [task_from_projection(task) for task in tasks]

    async def heartbeat(
        self, session: AsyncSession, job_id: UUID, owner: str, lease_seconds: int, *, now: datetime
    ) -> None:
        await projection_repo.heartbeat_projection_task(
            session, job_id, owner, lease_seconds, now=now
        )

    async def complete(
        self, session: AsyncSession, task: Task, owner: str, output: object, *, now: datetime
    ) -> None:
        ack = _projection_output(output)
        # Ack і є перехід task → `succeeded`: окремого `complete` для projection_tasks немає.
        # `owner` — fencing у тій самій транзакції (WP-01A PR3a п.7): чужий lease →
        # `LeaseNotOwnedError`, і жодного запису ack не з'являється.
        await projection_repo.acknowledge_projection(
            session, task.job_id, ack.receipt, event=ack.event, owner=owner, now=now
        )

    async def retry(
        self,
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        not_before: datetime | None,
        now: datetime,
    ) -> str:
        if not_before is None:
            task = await projection_repo.retry_projection_task(
                session, job_id, owner, error_code=error_code, error_message=error_message, now=now
            )
        else:
            task = await projection_repo.retry_projection_task(
                session,
                job_id,
                owner,
                error_code=error_code,
                error_message=error_message,
                policy=IMMEDIATE_POLICY,
                not_before=not_before,
                now=now,
            )
        return task.status

    async def defer(
        self, session: AsyncSession, job_id: UUID, owner: str, *, until: datetime, now: datetime
    ) -> None:
        await projection_repo.release_projection_task(
            session, job_id, owner, not_before=until, now=now
        )

    async def quarantine(
        self,
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        now: datetime,
    ) -> None:
        # `quarantine_projection_task` — операторський виклик без перевірки lease. Heartbeat
        # власником у тій самій транзакції бере row lock і кидає `LeaseNotOwnedError` для
        # чужого lease, тож worker не карантинить task, який уже виконує інший instance.
        await projection_repo.heartbeat_projection_task(session, job_id, owner, 1, now=now)
        await projection_repo.quarantine_projection_task(
            session, job_id, error_code=error_code, error_message=error_message, now=now
        )

    async def release(
        self, session: AsyncSession, job_id: UUID, owner: str, *, now: datetime
    ) -> None:
        await projection_repo.release_projection_task(session, job_id, owner, now=now)

    async def recover_expired(
        self, session: AsyncSession, *, limit: int, now: datetime
    ) -> list[UUID]:
        return await projection_repo.recover_expired_projection_leases(
            session, limit=limit, now=now
        )


CRAWL_JOBS: QueueBackend = CrawlJobsBackend()
PROJECTION_TASKS: QueueBackend = ProjectionTasksBackend()

__all__ = [
    "CRAWL_JOBS",
    "IMMEDIATE_POLICY",
    "PROJECTION_TASKS",
    "CrawlJobsBackend",
    "InvalidHandlerOutputError",
    "ProjectionAck",
    "ProjectionTasksBackend",
    "QueueBackend",
    "task_from_job",
    "task_from_projection",
]
