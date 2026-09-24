"""Композиція тіків scheduler-а проти PostgreSQL (WP-01D PR1c п.6; §7.3 п.5, §7.6, §9.3).

Scheduler під LOGIN-роллю `collector_scheduler`:

- вбудований maintenance-тік повертає прострочений lease `projection_tasks`
  (`recover_expired_projection_leases`), а не лише `crawl_jobs`;
- доменний тік, що кидає або зависає, не зупиняє maintenance-тік і не відпускає lease;
- два scheduler-и з однаковим доменним тіком (enqueue з ключем від вікна часу, §9.3 п.3) —
  і при чесній передачі lease, і при перекритті тіків — не дублюють жодної job
  (контракт ідемпотентності, вартовий `test_maintenance_tick_is_safe_when_two_schedulers_overlap`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from collector.persistence.postgres.models import CrawlJob, ProjectionTask
from collector.persistence.postgres.repositories import projection as projection_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.config import SchedulerRuntimeConfig
from collector.workers.registry import DomainTickSpec, LoadedTick
from collector.workers.scheduler import MAINTENANCE_TICK, SchedulerRuntime, TickContext

from .conftest import T0, WaitFor, make_entity, record_parse_result

pytestmark = pytest.mark.integration

MakePool = Callable[..., Awaitable[int]]
EnqueueJobs = Callable[..., Awaitable[list[UUID]]]

FAST = SchedulerRuntimeConfig(
    lease_name="scheduler-ticks", tick_seconds=0.02, lease_retry_seconds=0.02
)
RECONCILE_WINDOW_SECONDS = 300


async def schedule_reconcile(ctx: TickContext) -> None:
    """Доменний тік у формі WP-01B: лише enqueue з ідемпотентним ключем від вікна часу."""
    window = int(ctx.now.timestamp()) // RECONCILE_WINDOW_SECONDS
    async with ctx.transaction() as session:
        await queue_repo.enqueue(
            session,
            queue_repo.NewJob(
                job_type="projection.reconcile", idempotency_key=f"projection.reconcile:{window}"
            ),
            now=ctx.now,
        )


async def boom(ctx: TickContext) -> None:
    msg = "domain tick exploded"
    raise RuntimeError(msg)


async def hang(ctx: TickContext) -> None:
    await asyncio.sleep(3600)


def tick(
    name: str,
    run: Callable[[TickContext], Awaitable[None]],
    *,
    interval: float = 0.01,
    timeout: float = 5.0,
) -> LoadedTick:
    return LoadedTick(DomainTickSpec(name, f"tests:{name}", interval, timeout), run)


def start(runtime: SchedulerRuntime, running: list[asyncio.Task[None]]) -> asyncio.Task[None]:
    task = asyncio.create_task(runtime.run(install_signals=False))
    running.append(task)
    return task


async def test_default_tick_recovers_expired_projection_leases(
    pg_sessions: async_sessionmaker[AsyncSession],
    scheduler_engine: AsyncEngine,
    scheduler_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    async with pg_sessions() as session:
        entity = await make_entity(session)
        task_id = (await record_parse_result(session, entity.entity_uuid, 1)).task.task_id
    async with pg_sessions() as session, session.begin():
        # Lease «убитого» projector-а: видано в T0 на 60 с — давно прострочений.
        await projection_repo.claim_projection_tasks(session, "dead-projector", 60, now=T0)

    scheduler = SchedulerRuntime(FAST, scheduler_engine, scheduler_sessions, domain_ticks=())
    task = start(scheduler, running)
    await wait_for(lambda: scheduler.ticks >= 1, what="maintenance-тік виконано")
    async with pg_sessions() as session:
        projection_task = await session.get(ProjectionTask, task_id)
    assert projection_task is not None
    assert (projection_task.status, projection_task.lease_owner) == ("pending", None)
    assert projection_task.attempt == 1, "recover зберігає attempt"
    scheduler.request_stop()
    await asyncio.wait_for(task, timeout=15)


async def test_failing_or_hanging_domain_tick_does_not_stop_maintenance_or_lease(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    scheduler_engine: AsyncEngine,
    scheduler_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    async with pg_sessions() as session, session.begin():
        await queue_repo.claim(session, ["fetch"], "dead-instance", 60, now=T0)

    scheduler = SchedulerRuntime(
        FAST,
        scheduler_engine,
        scheduler_sessions,
        domain_ticks=[tick("boom", boom), tick("hang", hang, timeout=0.05)],
    )
    task = start(scheduler, running)
    await wait_for(
        lambda: (
            scheduler.tick_failures.get("boom", 0) >= 2
            and scheduler.tick_failures.get("hang", 0) >= 2
            and scheduler.tick_runs.get(MAINTENANCE_TICK, 0) >= 3
        ),
        what="доменні тіки падають, maintenance продовжується",
    )
    assert scheduler.is_active
    assert (scheduler.activations, scheduler.lease_losses) == (1, 0), "lease не відпущено"
    assert scheduler.tick_names == (MAINTENANCE_TICK, "boom", "hang")
    async with pg_sessions() as session:
        job = await session.get(CrawlJob, job_id)
    assert job is not None and job.status == "pending", "maintenance повернув lease"
    scheduler.request_stop()
    await asyncio.wait_for(task, timeout=15)


async def count_reconcile_jobs(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(CrawlJob)
                .where(CrawlJob.job_type == "projection.reconcile")
            )
            or 0
        )


async def test_two_schedulers_with_the_same_domain_tick_enqueue_no_duplicates(
    pg_sessions: async_sessionmaker[AsyncSession],
    scheduler_engine: AsyncEngine,
    scheduler_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    def fixed() -> datetime:
        return T0

    first = SchedulerRuntime(
        FAST,
        scheduler_engine,
        scheduler_sessions,
        domain_ticks=[tick("projection.reconcile", schedule_reconcile)],
        clock=fixed,
    )
    second = SchedulerRuntime(
        FAST,
        scheduler_engine,
        scheduler_sessions,
        domain_ticks=[tick("projection.reconcile", schedule_reconcile)],
        clock=fixed,
    )
    first_task = start(first, running)
    await wait_for(lambda: first.tick_runs.get("projection.reconcile", 0) >= 3, what="first тікає")
    second_task = start(second, running)
    await wait_for(lambda: second.attempted.is_set(), what="second пробував lease")
    first.request_stop()
    await asyncio.wait_for(first_task, timeout=15)
    await wait_for(
        lambda: second.tick_runs.get("projection.reconcile", 0) >= 3, what="second перейняв тік"
    )
    assert await count_reconcile_jobs(pg_sessions) == 1

    # Перекриття (lease зник між перевіркою і commit-ом): два тіки одночасно — той самий ключ.
    async def lease_is_ours() -> bool:
        return True

    contexts = [
        TickContext(
            name="projection.reconcile",
            sessions=scheduler_sessions,
            now=T0 + timedelta(seconds=1),
            statement_timeout_ms=FAST.statement_timeout_ms,
            lease_is_ours=lease_is_ours,
            env={},
        )
        for _ in range(2)
    ]
    await asyncio.gather(*(schedule_reconcile(ctx) for ctx in contexts))
    assert await count_reconcile_jobs(pg_sessions) == 1, "те саме вікно — та сама job"
    second.request_stop()
    await asyncio.wait_for(second_task, timeout=15)


async def test_domain_tick_sees_its_lease_and_opens_bounded_transactions(
    scheduler_engine: AsyncEngine,
    scheduler_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
) -> None:
    seen: list[tuple[bool, str]] = []

    async def probe(ctx: TickContext) -> None:
        async with ctx.transaction() as session:
            timeout = str(await session.scalar(select(func.current_setting("statement_timeout"))))
        seen.append((await ctx.lease_is_ours(), timeout))

    scheduler = SchedulerRuntime(
        FAST, scheduler_engine, scheduler_sessions, domain_ticks=[tick("probe", probe)]
    )
    task = start(scheduler, running)
    await wait_for(lambda: len(seen) >= 2, what="доменний тік виконано")
    assert all(ours for ours, _ in seen), "активний scheduler тримає lease"
    assert FAST.statement_timeout_ms == 1000
    assert seen[0][1] == "1s", "statement_timeout scheduler-а діє і в доменному тіку"
    scheduler.request_stop()
    await asyncio.wait_for(task, timeout=15)
