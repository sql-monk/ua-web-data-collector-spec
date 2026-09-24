"""Adversarial-перевірки WP-01D PR1c проти PostgreSQL від незалежного тестувальника.

Доповнюють `test_handler_plumbing.py` і `test_projection_backend.py`:

- `deferred` на **останній** спробі (`max_attempts=1`) → job `pending`, `attempt` 0, полів
  помилки й dead letter немає (retry тут дав би карантин);
- дубль ack: повторний `PROJECTION_TASKS.complete` для вже `succeeded` task тим самим owner-ом
  → `LeaseNotOwnedError`, рядок ack і подія лишаються в одному екземплярі;
- receipt чужої task → `ConflictError` у report-транзакції, жодного запису ack, task лишається
  за своїм lease (повертає recover);
- `output` на `crawl_jobs` → карантин `invalid_handler_output` + dead letter (задокументовано в
  `docs/workers.md` §5.4).

API WP-01A PR3a (`release(not_before=)`, `acknowledge_projection(owner=)`) merged у PR #12.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from collector.persistence.postgres.errors import LeaseNotOwnedError
from collector.persistence.postgres.models import (
    ChangeEvent,
    CrawlJob,
    DeadLetter,
    ProjectionAcknowledgement,
    ProjectionTask,
)
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.backends import PROJECTION_TASKS
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import HandlerBinding, Task, TaskHandler, TaskResult
from collector.workers.roles import WorkerRole
from collector.workers.runtime import WorkerRuntime

from .conftest import (
    PAST,
    PATIENT_TIMEOUT,
    PROJECTION_COLLECTION,
    T0,
    RoleSessions,
    WaitFor,
    make_entity,
    projection_receipt,
    record_parse_result,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def wait_for(patient_wait_for: WaitFor) -> WaitFor:
    return patient_wait_for


class Scripted(TaskHandler):
    def __init__(self, job_type: str, decide: Callable[[Task], TaskResult]) -> None:
        self._job_type = job_type
        self._decide = decide
        self.seen: list[Task] = []

    @property
    def job_types(self) -> tuple[str, ...]:
        return (self._job_type,)

    async def handle(self, task: Task) -> TaskResult:
        self.seen.append(task)
        return self._decide(task)


class Running:
    def __init__(self, runtime: WorkerRuntime, running: list[asyncio.Task[None]]) -> None:
        self.runtime = runtime
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(runtime.run(stop=self.stop, install_signals=False))
        running.append(self.task)

    async def close(self) -> None:
        self.stop.set()
        await asyncio.wait_for(self.task, timeout=PATIENT_TIMEOUT)


async def enqueue(sessions: async_sessionmaker[AsyncSession], *, max_attempts: int) -> UUID:
    async with sessions() as session, session.begin():
        job = await queue_repo.enqueue(
            session,
            queue_repo.NewJob(
                job_type="fetch", idempotency_key="adv:0", args={}, max_attempts=max_attempts
            ),
            now=PAST,
        )
        return job.job_id


async def count(sessions: async_sessionmaker[AsyncSession], stmt: object) -> int:
    async with sessions() as session:
        return int(await session.scalar(stmt) or 0)  # type: ignore[call-overload]


async def projection_task(sessions: async_sessionmaker[AsyncSession]) -> tuple[UUID, UUID]:
    async with sessions() as session:
        entity = await make_entity(session)
        entity_uuid = entity.entity_uuid
        return (await record_parse_result(session, entity_uuid, 1)).task.task_id, entity_uuid


def receipt_for(task: Task) -> TaskResult:
    entity_uuid, version = task.args["entity_uuid"], task.args["projection_version"]
    assert isinstance(entity_uuid, UUID) and isinstance(version, int)
    return TaskResult.success(
        output=projection_receipt(task.job_id, entity_uuid, version, applied=True, changed=True)
    )


# --- defer на останній спробі ---------------------------------------------------------------


async def test_defer_on_the_only_attempt_neither_quarantines_nor_writes_errors(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: Callable[..., object],
    worker_config: Callable[..., WorkerRuntimeConfig],
    runtime_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)  # type: ignore[misc]
    job_id = await enqueue(pg_sessions, max_attempts=1)
    until = T0 + timedelta(minutes=5)
    handler = Scripted("fetch", lambda _t: TaskResult.deferred(until, "permit_denied"))
    run = Running(
        WorkerRuntime(worker_config(), runtime_sessions, handler, clock=lambda: T0), running
    )

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт defer на останній спробі")
    assert handler.seen[0].attempt == handler.seen[0].max_attempts == 1
    assert run.runtime.report_failures == 0
    async with pg_sessions() as session:
        job = await session.get(CrawlJob, job_id)
    assert job is not None
    assert (job.status, job.attempt, job.not_before) == ("pending", 0, until)
    assert (job.last_error_code, job.last_error_message) == (None, None)
    assert await count(pg_sessions, select(func.count()).select_from(DeadLetter)) == 0
    await run.close()


# --- ack: дубль і чужий receipt -------------------------------------------------------------


async def test_duplicate_ack_by_the_same_owner_is_rejected_and_writes_nothing(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: Callable[..., object],
    worker_config: Callable[..., WorkerRuntimeConfig],
    role_sessions: RoleSessions,
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)  # type: ignore[misc]
    task_id, _ = await projection_task(pg_sessions)
    handler = Scripted(PROJECTION_COLLECTION, receipt_for)
    sessions = role_sessions(WorkerRole.PROJECTOR)
    run = Running(
        WorkerRuntime(
            worker_config(role=WorkerRole.PROJECTOR),
            sessions,
            [HandlerBinding(PROJECTION_TASKS, handler)],
        ),
        running,
    )
    await wait_for(lambda: run.runtime.reports >= 1, what="перший ack")
    assert run.runtime.report_failures == 0
    await run.close()

    (task,) = handler.seen
    with pytest.raises(LeaseNotOwnedError):
        async with sessions() as session, session.begin():
            await PROJECTION_TASKS.complete(
                session, task, run.runtime.owner, receipt_for(task).output, now=T0
            )
    ack_rows = select(func.count()).select_from(ProjectionAcknowledgement)
    assert await count(pg_sessions, ack_rows) == 1
    assert await count(pg_sessions, select(func.count()).select_from(ChangeEvent)) == 1
    async with pg_sessions() as session:
        stored = await session.get(ProjectionTask, task_id)
    assert stored is not None and stored.status == "succeeded"


async def test_receipt_of_another_task_is_a_conflict_and_leaves_no_ack(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: Callable[..., object],
    worker_config: Callable[..., WorkerRuntimeConfig],
    role_sessions: RoleSessions,
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)  # type: ignore[misc]
    task_id, entity_uuid = await projection_task(pg_sessions)
    foreign = projection_receipt(UUID(int=1), entity_uuid, 1, applied=True, changed=True)
    handler = Scripted(PROJECTION_COLLECTION, lambda _t: TaskResult.success(output=foreign))
    run = Running(
        WorkerRuntime(
            worker_config(role=WorkerRole.PROJECTOR),
            role_sessions(WorkerRole.PROJECTOR),
            [HandlerBinding(PROJECTION_TASKS, handler)],
        ),
        running,
    )
    with capture_logs() as logs:
        await wait_for(lambda: run.runtime.reports >= 1, what="звіт із чужим receipt")
    failed = [entry for entry in logs if entry["event"] == "worker.report_failed"]
    assert len(failed) == 1 and failed[0]["error"].startswith("ConflictError"), failed
    assert run.runtime.report_failures == 1
    ack_rows = select(func.count()).select_from(ProjectionAcknowledgement)
    assert await count(pg_sessions, ack_rows) == 0
    assert await count(pg_sessions, select(func.count()).select_from(ChangeEvent)) == 0
    async with pg_sessions() as session:
        stored = await session.get(ProjectionTask, task_id)
    assert stored is not None
    assert (stored.status, stored.lease_owner) == ("leased", run.runtime.owner)
    await run.close()


# --- output на crawl_jobs -------------------------------------------------------------------


async def test_output_on_crawl_jobs_quarantines_with_a_dead_letter(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: Callable[..., object],
    worker_config: Callable[..., WorkerRuntimeConfig],
    runtime_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)  # type: ignore[misc]
    job_id = await enqueue(pg_sessions, max_attempts=4)
    handler = Scripted("fetch", lambda _t: TaskResult.success(output={"password": "hunter2"}))
    run = Running(WorkerRuntime(worker_config(), runtime_sessions, handler), running)

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт з output на crawl_jobs")
    assert run.runtime.report_failures == 0
    async with pg_sessions() as session:
        job = await session.get(CrawlJob, job_id)
        letters = list(await session.scalars(select(DeadLetter).where(DeadLetter.job_id == job_id)))
    assert job is not None
    assert (job.status, job.last_error_code) == ("quarantined", "invalid_handler_output")
    assert "hunter2" not in (job.last_error_message or "")
    assert len(letters) == 1
    await run.close()
