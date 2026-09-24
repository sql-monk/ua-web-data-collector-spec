"""`ProjectionTasksBackend` і кілька прив'язок ролі проти PostgreSQL (WP-01D PR1c п.5).

Runtime ролі `projector` під LOGIN-роллю `collector_projector`; projection tasks готує WP-01A
`record_parse_result` (superuser, як оператор/міграції). Перевіряється:

- receipt handler-а → `acknowledge_projection` у **тій самій** транзакції, що й звіт: ack,
  task `succeeded`, рівно один рядок `projection_acknowledgements`; fault-seam між ack і commit →
  жодного часткового запису;
- lease забрали до звіту → ack не виконано (`lease_lost`); повторний claim іншим instance → рівно
  один ack;
- retry / defer / quarantine / drain-release projection task — як для `crawl_jobs`;
- output, що не є receipt → карантин `invalid_handler_output`;
- дві прив'язки (`projection_tasks` + `crawl_jobs` `projection.reconcile`) з
  `desired_concurrency=1` → обидві черги обслуговуються, жодна не голодує.

`NEEDS_PR3A` — потрібні `owner` в `acknowledge_projection` і `release_projection_task(not_before=)`
WP-01A PR3a; перевіряються після rebase (`xfail(strict=True)`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from itertools import groupby
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.models import (
    ChangeEvent,
    ProjectionAcknowledgement,
    ProjectionTask,
)
from collector.persistence.postgres.repositories import projection as projection_repo
from collector.workers.backends import CRAWL_JOBS, PROJECTION_TASKS, ProjectionTasksBackend
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import HandlerBinding, Task, TaskHandler, TaskResult
from collector.workers.roles import WorkerRole
from collector.workers.runtime import WorkerRuntime

from .conftest import (
    NEEDS_PR3A,
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

MakePool = Callable[..., Awaitable[int]]
EnqueueJobs = Callable[..., Awaitable[list[UUID]]]
MakeConfig = Callable[..., WorkerRuntimeConfig]


@pytest.fixture
def wait_for(patient_wait_for: WaitFor) -> WaitFor:
    """Бюджет `PATIENT_TIMEOUT` (conftest): очікування включає boot runtime під LOGIN-роллю."""
    return patient_wait_for


class FakeProjector(TaskHandler):
    """Замість Mongo-projector-а WP-01B: повертає receipt (або інший результат) для task."""

    def __init__(
        self,
        mode: str = "receipt",
        *,
        blocking: bool = False,
        order: list[str] | None = None,
    ) -> None:
        self.mode = mode
        self.release = asyncio.Event()
        if not blocking:
            self.release.set()
        self.started: list[UUID] = []
        self.order = order

    @property
    def job_types(self) -> tuple[str, ...]:
        return (PROJECTION_COLLECTION,)

    async def handle(self, task: Task) -> TaskResult:
        self.started.append(task.job_id)
        if self.order is not None:
            self.order.append("projection_tasks")
        await self.release.wait()
        if self.mode == "receipt":
            entity_uuid = task.args["entity_uuid"]
            version = task.args["projection_version"]
            assert isinstance(entity_uuid, UUID) and isinstance(version, int)
            return TaskResult.success(
                output=projection_receipt(
                    task.job_id, entity_uuid, version, applied=True, changed=True
                )
            )
        if self.mode == "retry":
            return TaskResult.retryable("mongo_timeout", "Mongo недоступний")
        if self.mode == "defer":
            return TaskResult.deferred(T0 + timedelta(minutes=10), "mongo_maintenance")
        if self.mode == "permanent":
            return TaskResult.permanent("payload_hash_mismatch")
        return TaskResult.success(output="not a receipt")


class ReconcileHandler(TaskHandler):
    """Друга прив'язка ролі `projector`: `crawl_jobs` типу `projection.reconcile`."""

    def __init__(self, order: list[str]) -> None:
        self.order = order

    @property
    def job_types(self) -> tuple[str, ...]:
        return ("projection.reconcile",)

    async def handle(self, task: Task) -> TaskResult:
        self.order.append("crawl_jobs")
        return TaskResult.success()


class FaultAfterAck(ProjectionTasksBackend):
    """Fault-seam: ack уже виконано в транзакції звіту, а commit не відбувся."""

    def __init__(self) -> None:
        self.acked = False

    async def complete(
        self, session: AsyncSession, task: Task, owner: str, output: object, *, now: datetime
    ) -> None:
        await super().complete(session, task, owner, output, now=now)
        self.acked = True
        msg = "fault between ack and commit"
        raise RuntimeError(msg)


async def projection_tasks(sessions: async_sessionmaker[AsyncSession], count: int) -> list[UUID]:
    """`count` projection tasks однієї сутності (версії 1..count) — через WP-01A."""
    async with sessions() as session:
        entity = await make_entity(session)
        entity_uuid = entity.entity_uuid
        return [
            (await record_parse_result(session, entity_uuid, n)).task.task_id
            for n in range(1, count + 1)
        ]


async def read_task(sessions: async_sessionmaker[AsyncSession], task_id: UUID) -> ProjectionTask:
    async with sessions() as session:
        task = await session.get(ProjectionTask, task_id)
    assert task is not None
    return task


async def count_rows(
    sessions: async_sessionmaker[AsyncSession], model: type[object], task_id: UUID | None = None
) -> int:
    stmt = select(func.count()).select_from(model)
    if model is ProjectionAcknowledgement and task_id is not None:
        stmt = stmt.where(ProjectionAcknowledgement.task_id == task_id)
    async with sessions() as session:
        return int(await session.scalar(stmt) or 0)


class Projector:
    """Runtime ролі `projector` під `collector_projector` з довільними прив'язками."""

    def __init__(
        self,
        config: WorkerRuntimeConfig,
        sessions: async_sessionmaker[AsyncSession],
        bindings: list[HandlerBinding],
        running: list[asyncio.Task[None]],
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.runtime = WorkerRuntime(config, sessions, bindings, clock=clock)
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(self.runtime.run(stop=self.stop, install_signals=False))
        running.append(self.task)

    async def close(self) -> None:
        self.stop.set()
        await asyncio.wait_for(self.task, timeout=PATIENT_TIMEOUT)


MakeProjector = Callable[..., Projector]


@pytest.fixture
def projector(
    worker_config: MakeConfig,
    role_sessions: RoleSessions,
    running: list[asyncio.Task[None]],
) -> MakeProjector:
    def make(
        *bindings: HandlerBinding,
        clock: Callable[[], datetime] = utcnow,
        **config: object,
    ) -> Projector:
        return Projector(
            worker_config(role=WorkerRole.PROJECTOR, **config),
            role_sessions(WorkerRole.PROJECTOR),
            list(bindings),
            running,
            clock=clock,
        )

    return make


# --- ack у report-транзакції ----------------------------------------------------------------


@NEEDS_PR3A
async def test_receipt_is_acknowledged_in_the_report_transaction(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    run = projector(HandlerBinding(PROJECTION_TASKS, FakeProjector()))

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт projector-а")
    task = await read_task(pg_sessions, task_id)
    assert run.runtime.report_failures == 0
    assert (task.status, task.lease_owner) == ("succeeded", None)
    assert await count_rows(pg_sessions, ProjectionAcknowledgement, task_id) == 1
    assert await count_rows(pg_sessions, ChangeEvent) == 1, "domain.changed з bytes receipt"
    await run.close()


@NEEDS_PR3A
async def test_fault_between_ack_and_commit_leaves_no_partial_rows(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    """Мутація «ack поза report-транзакцією» робить цей тест червоним: ack лишився б."""
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    backend = FaultAfterAck()
    run = projector(HandlerBinding(backend, FakeProjector()))

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт із fault-seam")
    assert backend.acked, "ack виконався в транзакції звіту, перш ніж її відкотили"
    assert run.runtime.report_failures == 1
    task = await read_task(pg_sessions, task_id)
    assert (task.status, task.lease_owner) == ("leased", run.runtime.owner)
    assert await count_rows(pg_sessions, ProjectionAcknowledgement) == 0
    assert await count_rows(pg_sessions, ChangeEvent) == 0
    await run.close()


@NEEDS_PR3A
async def test_lost_lease_blocks_the_ack_and_the_next_owner_acks_exactly_once(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    stale = FakeProjector(blocking=True)
    # heartbeat рідкісний: втрату lease runtime має виявити саме у звіті (fencing ack), а не
    # у heartbeat-і, який скасував би task раніше.
    first = projector(
        HandlerBinding(PROJECTION_TASKS, stale), lease_seconds=60, heartbeat_seconds=15
    )
    await wait_for(lambda: len(stale.started) == 1, what="перший instance узяв task")

    far = utcnow() + timedelta(days=1)
    async with pg_sessions() as session, session.begin():
        assert await projection_repo.recover_expired_projection_leases(session, now=far) == [
            task_id
        ]
        await projection_repo.claim_projection_tasks(session, "intruder", 60, now=far)
    stale.release.set()
    await wait_for(lambda: first.runtime.reports >= 1, what="звіт першого instance")
    assert (first.runtime.lost_leases, first.runtime.report_failures) == (1, 0)
    assert await count_rows(pg_sessions, ProjectionAcknowledgement) == 0
    await first.close()

    async with pg_sessions() as session, session.begin():
        await projection_repo.recover_expired_projection_leases(
            session, now=far + timedelta(days=1)
        )
    second = projector(HandlerBinding(PROJECTION_TASKS, FakeProjector()))
    await wait_for(lambda: second.runtime.reports >= 1, what="звіт другого instance")
    assert await count_rows(pg_sessions, ProjectionAcknowledgement, task_id) == 1
    assert (await read_task(pg_sessions, task_id)).status == "succeeded"
    await second.close()


# --- retry / defer / quarantine / drain ---------------------------------------------------


async def test_projection_task_retry_writes_the_error_and_burns_the_attempt(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    run = projector(HandlerBinding(PROJECTION_TASKS, FakeProjector("retry")))

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт retry")
    task = await read_task(pg_sessions, task_id)
    assert run.runtime.report_failures == 0
    assert (task.status, task.attempt, task.last_error_code) == ("retry", 1, "mongo_timeout")
    await run.close()


@NEEDS_PR3A
async def test_projection_task_defer_keeps_the_attempt(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    run = projector(HandlerBinding(PROJECTION_TASKS, FakeProjector("defer")), clock=lambda: T0)

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт defer")
    task = await read_task(pg_sessions, task_id)
    assert run.runtime.report_failures == 0
    assert (task.status, task.attempt, task.last_error_code) == ("pending", 0, None)
    assert task.not_before == T0 + timedelta(minutes=10)
    await run.close()


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [("permanent", "payload_hash_mismatch"), ("bad-output", "invalid_handler_output")],
)
async def test_projection_task_quarantine(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
    mode: str,
    error_code: str,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    run = projector(HandlerBinding(PROJECTION_TASKS, FakeProjector(mode)))

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт карантину")
    task = await read_task(pg_sessions, task_id)
    assert run.runtime.report_failures == 0
    assert (task.status, task.last_error_code, task.lease_owner) == (
        "quarantined",
        error_code,
        None,
    )
    assert await count_rows(pg_sessions, ProjectionAcknowledgement) == 0
    await run.close()


async def test_projection_quarantine_is_fenced_by_the_lease_owner(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    """`quarantine_projection_task` сам lease не перевіряє — backend фенсить його heartbeat-ом."""
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    handler = FakeProjector("permanent", blocking=True)
    run = projector(
        HandlerBinding(PROJECTION_TASKS, handler), lease_seconds=60, heartbeat_seconds=15
    )
    await wait_for(lambda: len(handler.started) == 1, what="task узято")
    far = utcnow() + timedelta(days=1)
    async with pg_sessions() as session, session.begin():
        await projection_repo.recover_expired_projection_leases(session, now=far)
        await projection_repo.claim_projection_tasks(session, "intruder", 60, now=far)
    handler.release.set()
    await wait_for(lambda: run.runtime.reports >= 1, what="звіт карантину")
    task = await read_task(pg_sessions, task_id)
    assert run.runtime.lost_leases == 1
    assert (task.status, task.lease_owner) == ("leased", "intruder"), "чужу task не чіпаємо"
    await run.close()


async def test_drain_returns_the_projection_lease_without_burning_the_attempt(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    (task_id,) = await projection_tasks(pg_sessions, 1)
    handler = FakeProjector(blocking=True)
    run = projector(HandlerBinding(PROJECTION_TASKS, handler), stop_grace_seconds=0.2)
    await wait_for(lambda: len(handler.started) == 1, what="task у роботі")

    await run.close()
    task = await read_task(pg_sessions, task_id)
    assert (task.status, task.attempt, task.lease_owner) == ("pending", 0, None)
    assert task.last_error_code is None


# --- дві прив'язки ролі ---------------------------------------------------------------------


async def test_two_bindings_with_one_slot_serve_both_queues(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    projector: MakeProjector,
    wait_for: WaitFor,
) -> None:
    await make_pool(role=WorkerRole.PROJECTOR, concurrency=1)
    await projection_tasks(pg_sessions, 10)
    await enqueue_jobs(10, job_type="projection.reconcile", prefix="reconcile")
    order: list[str] = []
    run = projector(
        # Карантин замість ack: цей тест про чесність claim, а не про ack (він — вище).
        HandlerBinding(PROJECTION_TASKS, FakeProjector("permanent", order=order)),
        HandlerBinding(CRAWL_JOBS, ReconcileHandler(order)),
    )

    await wait_for(lambda: len(order) >= 12, what="12 tasks з обох черг")
    await run.close()
    window = order[:12]
    assert set(window) == {"projection_tasks", "crawl_jobs"}
    longest = max(len(list(group)) for _, group in groupby(window))
    assert longest <= 2, f"одна черга голодує іншу: {window}"
    assert run.runtime.claims_by_backend["projection_tasks"] >= 5
    assert run.runtime.claims_by_backend["crawl_jobs"] >= 5
