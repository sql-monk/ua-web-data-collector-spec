"""Adversarial-сценарії singleton scheduler (WP-01D PR1; §7.5, §7.6, вимога 6 картки).

Доповнення до `test_scheduler_singleton.py`:

- резервний scheduler не робить **жодної** maintenance-роботи, доки lease не його: прострочений
  lease повертається рівно один раз, і саме активним процесом;
- lease ізольований за іменем: `scheduler` і `controller` — різні singleton-и, вони не
  блокують один одного (інакше запуск controller-а в PR3 «вимкнув» би планувальник);
- graceful stop активного процесу віддає lease тут і зараз, без очікування TTL, і другий
  процес стає активним (kill-сценарій уже покритий `pg_terminate_backend` у базовому файлі).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from collector.persistence.postgres.models import CrawlJob
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.advisory import AdvisoryLease, advisory_key
from collector.workers.config import SchedulerRuntimeConfig
from collector.workers.scheduler import (
    MaintenanceResult,
    SchedulerRuntime,
    SchedulerTick,
    run_maintenance,
)

from .conftest import T0, WaitFor

pytestmark = pytest.mark.integration

MakePool = Callable[..., Awaitable[int]]
EnqueueJobs = Callable[..., Awaitable[list[UUID]]]

FAST = SchedulerRuntimeConfig(
    lease_name="scheduler-adversarial", tick_seconds=0.02, lease_retry_seconds=0.02
)


def recording_maintenance(results: list[MaintenanceResult]) -> SchedulerTick:
    """Справжній maintenance-тік, який додатково записує свій результат."""

    async def tick(session: AsyncSession, now: datetime) -> None:
        results.append(await run_maintenance(session, now, config=FAST))

    return tick


def counting_tick(calls: list[datetime]) -> SchedulerTick:
    """Тік без побічних ефектів, який лише фіксує факт виклику."""

    async def tick(session: AsyncSession, now: datetime) -> None:
        calls.append(now)

    return tick


def start(runtime: SchedulerRuntime, running: list[asyncio.Task[None]]) -> asyncio.Task[None]:
    task = asyncio.create_task(runtime.run(install_signals=False))
    running.append(task)
    return task


async def test_standby_scheduler_does_no_maintenance_while_the_active_one_does(
    pg_engine: AsyncEngine,
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Прострочений lease повертає рівно один процес — той, що тримає advisory lease."""
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    async with pg_sessions() as session, session.begin():
        # lease виданий у T0 на 60 с → на момент реального тіку він давно прострочений.
        claimed = await queue_repo.claim(session, ["fetch"], "dead-instance", 60, now=T0)
    assert [job.job_id for job in claimed] == [job_id]

    results_a: list[MaintenanceResult] = []
    results_b: list[MaintenanceResult] = []
    first = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=recording_maintenance(results_a))
    second = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=recording_maintenance(results_b))
    tasks = [start(first, running), start(second, running)]

    await wait_for(lambda: first.is_active or second.is_active, what="хтось узяв lease")
    active, standby = (first, second) if first.is_active else (second, first)
    standby_results = results_b if active is first else results_a
    active_results = results_a if active is first else results_b

    await wait_for(lambda: active.ticks >= 3, what="активний виконав кілька тіків")
    await wait_for(lambda: standby.acquire_attempts >= 3, what="резервний лише пробує lease")

    assert standby_results == [], "резервний scheduler не має торкатися черги"
    assert standby.ticks == 0
    recovered = sum(result.recovered_leases for result in active_results)
    assert recovered == 1, "прострочений lease повернуто рівно один раз"

    async with pg_sessions() as session:
        job = await session.get(CrawlJob, job_id)
    assert job is not None
    assert (job.status, job.lease_owner, job.attempt) == ("pending", None, 1)

    first.request_stop()
    second.request_stop()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)


async def test_named_leases_are_isolated_from_each_other(pg_engine: AsyncEngine) -> None:
    """`scheduler` і `controller` — окремі singleton-и: один не блокує іншого."""
    assert advisory_key("scheduler") != advisory_key("controller")
    scheduler_lease = AdvisoryLease(pg_engine, "scheduler")
    controller_lease = AdvisoryLease(pg_engine, "controller")
    try:
        assert await scheduler_lease.try_acquire() is True
        assert await controller_lease.try_acquire() is True, (
            "інше ім'я lease не має конкурувати зі scheduler-ом"
        )
        assert await scheduler_lease.is_held() is True
        assert await controller_lease.is_held() is True
    finally:
        await scheduler_lease.release()
        await controller_lease.release()


async def test_graceful_stop_hands_the_lease_over_without_waiting_for_a_ttl(
    pg_engine: AsyncEngine,
    pg_sessions: async_sessionmaker[AsyncSession],
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """SIGTERM активного → lease вільний одразу (session-scoped lock, без sweeper-а)."""
    calls_a: list[datetime] = []
    calls_b: list[datetime] = []
    first = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=counting_tick(calls_a))
    second = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=counting_tick(calls_b))
    first_task = start(first, running)
    await wait_for(lambda: first.is_active, what="перший активний")
    second_task = start(second, running)
    await wait_for(lambda: second.acquire_attempts >= 2, what="другий чекає")
    assert not second.is_active

    before = len(calls_a)
    first.request_stop()
    await asyncio.wait_for(first_task, timeout=15)
    assert not first.is_active
    assert await first.lease.is_held() is False

    await wait_for(lambda: second.is_active, what="lease перейшов без очікування TTL")
    await wait_for(lambda: second.ticks >= 1, what="новий активний планує")
    assert len(calls_a) >= before, "зупинений процес більше не планує"
    stopped_at = len(calls_a)
    await wait_for(lambda: second.ticks >= 3, what="ще кілька тіків нового активного")
    assert len(calls_a) == stopped_at

    second.request_stop()
    await asyncio.wait_for(second_task, timeout=15)


async def test_active_scheduler_ticks_only_while_the_server_confirms_the_lease(
    pg_engine: AsyncEngine,
    pg_sessions: async_sessionmaker[AsyncSession],
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Втрата зʼєднання з PG: планування припиняється до повторного `try_acquire`.

    Lease забирається «ззовні» — `pg_terminate_backend` по backend-у, який його тримає; це
    точний еквівалент розриву мережі/kill контейнера з боку сервера.
    """
    calls: list[datetime] = []
    runtime = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=counting_tick(calls))
    task = start(runtime, running)
    await wait_for(lambda: runtime.ticks >= 2, what="активний планує")

    pid = runtime.lease.backend_pid
    assert pid is not None
    async with pg_engine.connect() as connection:
        await connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    await wait_for(lambda: runtime.lease_losses >= 1, what="втрату lease помічено")
    assert not runtime.is_active, "без підтвердженого lease процес не активний"
    frozen = len(calls)

    await wait_for(lambda: runtime.activations >= 2, what="lease взято наново")
    assert len(calls) >= frozen, "після повторного захоплення планування триває"
    assert runtime.lease.backend_pid not in (None, pid), "нова сесія, новий backend"

    runtime.request_stop()
    await asyncio.wait_for(task, timeout=15)
