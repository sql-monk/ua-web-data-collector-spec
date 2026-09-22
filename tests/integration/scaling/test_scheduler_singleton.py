"""Singleton scheduler: advisory lease, takeover, втрата lease, maintenance tick (WP-01D PR1).

Сценарій картки «два scheduler → рівно один активний» плюс §7.5 «scheduler має singleton
advisory lease» і §15/§7.6 fault case: прострочені lease повертає maintenance-тік.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from collector.contracts import new_entity_id
from collector.persistence.postgres.models import CrawlJob, WorkerInstance
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.advisory import AdvisoryLease
from collector.workers.config import SchedulerRuntimeConfig
from collector.workers.roles import WorkerRole
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
    lease_name="scheduler-test", tick_seconds=0.02, lease_retry_seconds=0.02
)


def counting_tick(calls: list[datetime]) -> SchedulerTick:
    async def tick(session: AsyncSession, now: datetime) -> None:
        calls.append(now)

    return tick


def start(runtime: SchedulerRuntime, running: list[asyncio.Task[None]]) -> asyncio.Task[None]:
    task = asyncio.create_task(runtime.run(install_signals=False))
    running.append(task)
    return task


async def terminate_backend(engine: AsyncEngine, pid: int | None) -> None:
    """Обірвати сесію, яка тримає advisory lease (еквівалент kill контейнера scheduler-а)."""
    assert pid is not None
    async with engine.connect() as connection:
        await connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})


async def test_two_schedulers_keep_exactly_one_active_and_standby_takes_over(
    pg_engine: AsyncEngine,
    pg_sessions: async_sessionmaker[AsyncSession],
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    calls_a: list[datetime] = []
    calls_b: list[datetime] = []
    first = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=counting_tick(calls_a))
    second = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=counting_tick(calls_b))
    start(first, running)
    start(second, running)

    await wait_for(lambda: first.is_active or second.is_active, what="хтось узяв lease")
    active, standby = (first, second) if first.is_active else (second, first)

    await wait_for(
        lambda: standby.acquire_attempts >= 3, what="резервний scheduler пробує взяти lease"
    )
    assert not standby.is_active, "другий instance не стає активним, а чекає"
    assert standby.ticks == 0, "резервний нічого не планує"
    await wait_for(lambda: active.ticks >= 2, what="активний scheduler планує")

    await terminate_backend(pg_engine, active.lease.backend_pid)
    await wait_for(lambda: standby.is_active, what="резервний перебрав lease після смерті першого")
    await wait_for(lambda: active.lease_losses >= 1, what="перший помітив втрату lease")
    assert not (first.is_active and second.is_active), "двох активних не буває"

    frozen = active.ticks
    attempts = active.acquire_attempts
    await wait_for(
        lambda: active.acquire_attempts >= attempts + 3, what="колишній активний лише чекає"
    )
    assert active.ticks == frozen, "без lease планування припинено"
    await wait_for(lambda: standby.ticks >= 1, what="новий активний планує")

    first.request_stop()
    second.request_stop()
    await asyncio.wait_for(asyncio.gather(*running), timeout=15)


async def test_single_scheduler_reacquires_lease_before_planning_again(
    pg_engine: AsyncEngine,
    pg_sessions: async_sessionmaker[AsyncSession],
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Після втрати lease планування відновлюється лише через нове `try_acquire`."""
    active_flags: list[bool] = []
    holder: list[SchedulerRuntime] = []

    async def tick(session: AsyncSession, now: datetime) -> None:
        active_flags.append(holder[0].is_active)

    runtime = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=tick)
    holder.append(runtime)
    task = start(runtime, running)
    await wait_for(lambda: runtime.ticks >= 1, what="перший тік")

    await terminate_backend(pg_engine, runtime.lease.backend_pid)
    await wait_for(lambda: runtime.lease_losses >= 1, what="втрату lease помічено")
    await wait_for(lambda: runtime.activations >= 2, what="lease взято наново")
    await wait_for(lambda: runtime.ticks >= 2, what="планування відновлено")
    assert all(active_flags), "тік виконується лише з lease на руках"

    runtime.request_stop()
    await asyncio.wait_for(task, timeout=15)
    assert not runtime.is_active


async def test_advisory_lease_is_exclusive_and_survives_only_its_session(
    pg_engine: AsyncEngine,
) -> None:
    first = AdvisoryLease(pg_engine, "scheduler-exclusive")
    second = AdvisoryLease(pg_engine, "scheduler-exclusive")
    assert await first.try_acquire() is True
    assert await second.try_acquire() is False
    assert await first.is_held() is True
    assert await second.is_held() is False

    await first.release()
    assert await first.is_held() is False
    assert await second.try_acquire() is True
    assert await second.is_held() is True

    await terminate_backend(pg_engine, second.backend_pid)
    assert await second.is_held() is False, "мертва сесія не тримає lease"
    await second.release()

    third = AdvisoryLease(pg_engine, "scheduler-exclusive")
    assert await third.try_acquire() is True
    await third.release()


async def test_maintenance_tick_recovers_expired_leases_and_marks_stale_instances(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
) -> None:
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    instance_id = new_entity_id()
    async with pg_sessions() as session, session.begin():
        await queue_repo.claim(session, ["fetch"], "dead-instance", 60, now=T0)
        await pools_repo.register_instance(
            session,
            instance_id,
            WorkerRole.FETCH,
            version="0.1.0+test",
            slots_total=1,
            now=T0,
        )

    config = SchedulerRuntimeConfig(stale_after_seconds=60)
    async with pg_sessions() as session, session.begin():
        result = await run_maintenance(session, T0 + timedelta(hours=1), config=config)
    assert result == MaintenanceResult(recovered_leases=1, stale_instances=1)

    async with pg_sessions() as session:
        job = await session.get(CrawlJob, job_id)
        instance = await session.get(WorkerInstance, instance_id)
    assert job is not None and instance is not None
    assert (job.status, job.lease_owner, job.attempt) == ("pending", None, 1)
    assert instance.status == "stale"

    # Ідемпотентність: повторний прохід не має що відновлювати.
    async with pg_sessions() as session, session.begin():
        again = await run_maintenance(session, T0 + timedelta(hours=1), config=config)
    assert again == MaintenanceResult(recovered_leases=0, stale_instances=0)
