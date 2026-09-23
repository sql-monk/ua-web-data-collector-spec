"""Adversarial-сценарії worker runtime (WP-01D PR1; §7.5, §7.6, §15, FR-031/FR-032, R-52, R-57).

Доповнення до `test_worker_runtime.py`: тут перевіряється не «щасливий шлях», а те, що
runtime **не** робить у сумнівних ситуаціях —

- два runtime claim-лять одну job: виграє рівно один, `attempt` не збільшується двічі;
- task висить довше за lease без heartbeat: lease **не** продовжується сам, а після втрати
  власності runtime не звітує тихий `complete`;
- `recover_expired_leases` не забирає job у живого instance, який heartbeat-ить;
- `drain_requested_at` — барʼєр per-instance: інші instances ролі claim-лять далі, доки барʼєр
  не поставлено кожному (саме тому R-57 вимагає role-wide барʼєра від контролера, а не
  покладання на вибір контейнера orchestrator-ом);
- повторний SIGTERM під активним task нічого не ламає, exit лишається штатним;
- instance у `stopped` не claim-ить і процес завершується;
- повторний boot того самого контейнера дає НОВИЙ `worker_instance_id` (§7.5: hostname — лише
  metadata; §15: жодного стану на локальному диску);
- `mark_stale_instances` не чіпає instance зі свіжим heartbeat;
- `PermanentTaskError` доводиться до `quarantine` + dead letter, а не до `retry`.

Час не вимірюється wall-clock: «прострочення» моделюється явним `now` у репозиторії, а
очікування — предикатами `wait_for` над станом БД/лічильниками runtime.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts import new_entity_id
from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.models import CrawlJob, DeadLetter, WorkerInstance
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import PermanentTaskError
from collector.workers.roles import WorkerRole
from collector.workers.runtime import WorkerRuntime

from .conftest import ControlledHandler, WaitFor

pytestmark = pytest.mark.integration

MakePool = Callable[..., Awaitable[int]]
EnqueueJobs = Callable[..., Awaitable[list[UUID]]]
MakeConfig = Callable[..., WorkerRuntimeConfig]


def start(
    runtime: WorkerRuntime, running: list[asyncio.Task[None]], stop: asyncio.Event
) -> asyncio.Task[None]:
    task = asyncio.create_task(runtime.run(stop=stop, install_signals=False))
    running.append(task)
    return task


async def read_job(sessions: async_sessionmaker[AsyncSession], job_id: UUID) -> CrawlJob:
    async with sessions() as session:
        job = await session.get(CrawlJob, job_id)
    assert job is not None
    return job


async def read_instance(
    sessions: async_sessionmaker[AsyncSession], instance_id: UUID
) -> WorkerInstance:
    async with sessions() as session:
        instance = await session.get(WorkerInstance, instance_id)
    assert instance is not None
    return instance


async def settle(*runtimes: WorkerRuntime, wait_for: WaitFor, beats: int = 3) -> None:
    """Дочекатися `beats` heartbeat-ів кожного runtime — «минуло достатньо циклів loop-у».

    Використовується там, де треба довести **відсутність** дії (job не взято, lease не
    продовжено): лічильник heartbeat-ів — детермінований замінник `sleep`.
    """
    marks = [runtime.heartbeats + beats for runtime in runtimes]
    await wait_for(
        lambda: all(
            runtime.heartbeats >= mark for runtime, mark in zip(runtimes, marks, strict=True)
        ),
        what=f"{beats} heartbeat-ів кожного runtime",
    )


async def test_two_runtimes_claiming_one_job_exactly_one_wins(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`FOR UPDATE SKIP LOCKED`: одну job виконує рівно один instance, `attempt` рівно 1."""
    await make_pool(concurrency=4)
    (job_id,) = await enqueue_jobs(1)
    first_handler = ControlledHandler()
    second_handler = ControlledHandler()
    first = WorkerRuntime(worker_config(), runtime_sessions, first_handler)
    second = WorkerRuntime(worker_config(), runtime_sessions, second_handler)
    stop = asyncio.Event()
    tasks = [start(first, running, stop), start(second, running, stop)]

    await wait_for(
        lambda: job_id in first_handler.finished or job_id in second_handler.finished,
        what="job виконано одним із instances",
    )
    await settle(first, second, wait_for=wait_for)

    started = first_handler.started + second_handler.started
    assert started.count(job_id) == 1, "job не має потрапити до двох instances"
    job = await read_job(pg_sessions, job_id)
    assert job.status == "succeeded"
    assert job.attempt == 1, "другий claim збільшив би attempt"
    assert job.lease_owner is None
    assert (first.lost_leases, second.lost_leases) == (0, 0)

    stop.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)


async def test_hanging_task_does_not_extend_its_lease_and_never_reports_a_silent_complete(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Без heartbeat lease не продовжується; після recovery звіт власника відхиляється."""
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    # heartbeat навмисно не встигне спрацювати за час тесту: lease тримається лише claim-ом.
    # (heartbeat ≤ ⅓ lease — межа після L-1 код-рев'ю; 40 с так само не спрацює за час тесту.)
    runtime = WorkerRuntime(
        worker_config(lease_seconds=120, heartbeat_seconds=40.0), runtime_sessions, blocking_handler
    )
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="task у роботі")

    claimed = await read_job(pg_sessions, job_id)
    expiry = claimed.lease_expires_at
    assert expiry is not None
    assert runtime.heartbeats == 0, "тест має пройти без жодного heartbeat"
    assert (await read_job(pg_sessions, job_id)).lease_expires_at == expiry, (
        "lease не продовжується сам собою, доки task виконується"
    )

    async with pg_sessions() as session, session.begin():
        recovered = await queue_repo.recover_expired_leases(
            session, now=expiry + timedelta(seconds=1)
        )
    assert recovered == [job_id], "після експірації job повертається у чергу"
    assert blocking_handler.in_flight == 1, "handler усе ще працює — runtime про це не знає"

    # Зупиняємо claim, щоб цей самий instance не взяв job наново і не замаскував головну
    # перевірку: результат роботи над job-ою, яку в нас забрали, не має ставати `succeeded`.
    stop.set()
    blocking_handler.release.set()
    await asyncio.wait_for(task, timeout=15)

    assert runtime.lost_leases >= 1, "звіт про результат відхилено як не-власника"
    job = await read_job(pg_sessions, job_id)
    assert job.status == "pending", "runtime не має тихо завершувати job, яку в нього забрали"
    assert job.lease_owner is None
    assert job.finished_at is None


async def test_recover_expired_leases_spares_a_live_heartbeating_task(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Maintenance-прохід під активним heartbeat не забирає job у живого instance."""
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    runtime = WorkerRuntime(
        worker_config(lease_seconds=60, heartbeat_seconds=0.05), runtime_sessions, blocking_handler
    )
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="task у роботі")
    await settle(runtime, wait_for=wait_for)

    first_expiry = (await read_job(pg_sessions, job_id)).lease_expires_at
    for _ in range(3):
        async with pg_sessions() as session, session.begin():
            assert await queue_repo.recover_expired_leases(session) == [], (
                "живий lease не прострочений — забирати його не можна"
            )
    job = await read_job(pg_sessions, job_id)
    assert (job.status, job.lease_owner) == ("leased", runtime.owner)
    assert runtime.lost_leases == 0

    await settle(runtime, wait_for=wait_for)
    assert first_expiry is not None
    later = (await read_job(pg_sessions, job_id)).lease_expires_at
    assert later is not None and later > first_expiry, "heartbeat рухає lease вперед"

    blocking_handler.release.set()
    await wait_for(lambda: job_id in blocking_handler.finished, what="task завершено")
    await wait_for(lambda: runtime.active_tasks == 0, what="результат відзвітовано")
    assert (await read_job(pg_sessions, job_id)).status == "succeeded"

    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_drain_barrier_must_be_set_on_every_instance_of_the_role(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """R-57: барʼєр діє per-instance, тож зупинка ролі = барʼєр на КОЖНОМУ instance.

    Перша половина тесту фіксує, що барʼєр на одному instance не зупиняє решту ролі (інакше
    «role-wide» був би випадковим побічним ефектом), друга — що з барʼєром на всіх instances
    жодна нова job не береться.
    """
    await make_pool(concurrency=1)
    first_handler = ControlledHandler()
    second_handler = ControlledHandler()
    first = WorkerRuntime(worker_config(), runtime_sessions, first_handler)
    second = WorkerRuntime(worker_config(), runtime_sessions, second_handler)
    stop = asyncio.Event()
    tasks = [start(first, running, stop), start(second, running, stop)]
    await wait_for(
        lambda: first.status == "ready" and second.status == "ready", what="обидва instances ready"
    )

    async with pg_sessions() as session, session.begin():
        await pools_repo.mark_draining(session, first.instance_id)
    await wait_for(lambda: not first.claiming, what="барʼєр зупинив перший instance")

    (job_id,) = await enqueue_jobs(1)
    await wait_for(lambda: job_id in second_handler.finished, what="решта ролі claim-ить далі")
    assert first_handler.started == [], "instance під барʼєром не бере jobs"

    async with pg_sessions() as session, session.begin():
        await pools_repo.mark_draining(session, second.instance_id)
    await wait_for(lambda: not second.claiming, what="барʼєр на всій ролі")

    (blocked_id,) = await enqueue_jobs(1, prefix="blocked")
    await settle(first, second, wait_for=wait_for)
    assert (await read_job(pg_sessions, blocked_id)).status == "pending", (
        "role-wide барʼєр: жоден instance ролі не claim-ить"
    )
    assert first_handler.started == []
    assert second_handler.started == [job_id]

    stop.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)


async def test_repeated_stop_requests_under_an_active_task_are_idempotent(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Другий SIGTERM у межах grace не скасовує активний task і не змінює результат drain."""
    await make_pool(concurrency=1)
    job_ids = await enqueue_jobs(2)
    runtime = WorkerRuntime(
        worker_config(stop_grace_seconds=10.0), runtime_sessions, blocking_handler
    )
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="task у роботі")

    runtime.request_stop()
    await wait_for(lambda: runtime.status == "draining", what="перший SIGTERM → draining")
    runtime.request_stop()
    runtime.request_stop()
    assert runtime.active_tasks == 1, "повторний сигнал не вбиває активний task"

    blocking_handler.release.set()
    await asyncio.wait_for(task, timeout=15)

    assert blocking_handler.cancelled == [], "task завершився сам, його не скасували"
    assert runtime.lost_leases == 0
    statuses = [(await read_job(pg_sessions, job_id)).status for job_id in job_ids]
    assert sorted(statuses) == ["pending", "succeeded"]
    instance = await read_instance(pg_sessions, runtime.instance_id)
    assert (instance.status, instance.active_leases) == ("stopped", 0)


async def test_instance_marked_stopped_claims_nothing_and_exits(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`worker_instances.status = stopped` → heartbeat відхилено, процес завершується без claim."""
    await make_pool(concurrency=1)
    runtime = WorkerRuntime(worker_config(), runtime_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.status == "ready", what="ready")

    # Спершу барʼєр — щоб job гарантовано не була взята до того, як instance зупинять.
    async with pg_sessions() as session, session.begin():
        await pools_repo.mark_draining(session, runtime.instance_id)
    await wait_for(lambda: not runtime.claiming, what="claim зупинено барʼєром")
    (job_id,) = await enqueue_jobs(1)

    async with pg_sessions() as session, session.begin():
        await pools_repo.mark_stopped(session, runtime.instance_id)
    await asyncio.wait_for(task, timeout=15), "stopped instance має завершити процес"

    assert handler.started == [], "зупинений instance не бере tasks"
    assert (await read_job(pg_sessions, job_id)).status == "pending"
    assert (await read_instance(pg_sessions, runtime.instance_id)).status == "stopped"


async def test_second_boot_of_the_same_container_creates_a_new_instance(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    make_pool: MakePool,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """§7.5/§15: `worker_instance_id` — boot UUIDv7, hostname/container_id лише metadata."""
    await make_pool(concurrency=1)
    config = worker_config()
    first = WorkerRuntime(config, runtime_sessions, ControlledHandler())
    first_stop = asyncio.Event()
    first_task = start(first, running, first_stop)
    await wait_for(lambda: first.status == "ready", what="перший boot ready")
    first_stop.set()
    await asyncio.wait_for(first_task, timeout=15)

    second = WorkerRuntime(config, runtime_sessions, ControlledHandler())
    second_stop = asyncio.Event()
    second_task = start(second, running, second_stop)
    await wait_for(lambda: second.status == "ready", what="другий boot ready")

    assert second.instance_id != first.instance_id, "перезапуск контейнера = новий instance"
    assert second.instance_id.version == 7
    async with pg_sessions() as session:
        rows = list(
            (
                await session.execute(
                    select(WorkerInstance).where(
                        WorkerInstance.container_id == config.container_id,
                        WorkerInstance.role == WorkerRole.FETCH.value,
                    )
                )
            ).scalars()
        )
    assert {row.instance_id for row in rows} == {first.instance_id, second.instance_id}
    statuses = {row.instance_id: row.status for row in rows}
    assert statuses[first.instance_id] == "stopped"
    assert statuses[second.instance_id] == "ready"
    assert {row.hostname for row in rows} == {config.hostname}

    second_stop.set()
    await asyncio.wait_for(second_task, timeout=15)


async def test_stale_marking_spares_an_instance_with_a_fresh_heartbeat(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`stale` визначається віком heartbeat: живий instance лишається `ready`."""
    await make_pool(concurrency=1)
    runtime = WorkerRuntime(worker_config(heartbeat_seconds=0.05), runtime_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.status == "ready", what="ready")
    await wait_for(lambda: runtime.heartbeats >= 2, what="heartbeat-и йдуть")

    dead_id = new_entity_id()
    now = utcnow()
    async with pg_sessions() as session, session.begin():
        await pools_repo.register_instance(
            session,
            dead_id,
            WorkerRole.FETCH,
            version="0.1.0+dead",
            slots_total=1,
            now=now - timedelta(hours=1),
        )
    async with pg_sessions() as session, session.begin():
        stale = await pools_repo.mark_stale_instances(
            session, heartbeat_ttl=timedelta(seconds=60), now=now
        )
    assert stale == [dead_id], "без heartbeat довше TTL — і тільки такий instance"
    assert (await read_instance(pg_sessions, runtime.instance_id)).status == "ready"
    assert (await read_instance(pg_sessions, dead_id)).status == "stale"

    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_permanent_handler_error_quarantines_with_a_dead_letter(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`PermanentTaskError` → `quarantined` + dead letter, а не нескінченний retry."""
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    handler.raise_error = PermanentTaskError("схема джерела змінилась", error_code="schema_drift")
    runtime = WorkerRuntime(worker_config(), runtime_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)

    await wait_for(lambda: job_id in handler.finished, what="handler кинув permanent-помилку")
    await wait_for(lambda: runtime.active_tasks == 0, what="результат відзвітовано")

    job = await read_job(pg_sessions, job_id)
    assert job.status == "quarantined"
    assert job.last_error_code == "schema_drift"
    assert job.lease_owner is None
    async with pg_sessions() as session:
        letters = list(
            (await session.execute(select(DeadLetter).where(DeadLetter.job_id == job_id))).scalars()
        )
    assert len(letters) == 1, "карантин супроводжується рівно одним dead letter"
    assert letters[0].error_code == "schema_drift"

    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_drain_timeout_on_the_last_attempt_never_quarantines_a_job_nobody_failed(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    runtime_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Знахідка F2 (закрита PR1b п.5): плановий drain не «спалює» job на останній спробі.

    `queue.retry` на `attempt >= max_attempts` переводить job у `quarantined` + dead letter
    `max_attempts`, але job ніхто не провалив — просто зупинили контейнер. Тепер
    `_release_leases` викликає `queue.release` (WP-01A PR2): job одразу `pending` (без
    очікування експірації lease), `attempt` повертається до значення до claim, жодного
    карантину, dead letter чи полів помилки.
    """
    await make_pool(concurrency=1)
    async with pg_sessions() as session, session.begin():
        job = await queue_repo.enqueue(
            session,
            queue_repo.NewJob(
                job_type="fetch", idempotency_key="last-attempt", args={}, max_attempts=1
            ),
        )
        job_id = job.job_id

    runtime = WorkerRuntime(
        worker_config(stop_grace_seconds=0.2), runtime_sessions, blocking_handler
    )
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="остання спроба у роботі")
    assert (await read_job(pg_sessions, job_id)).attempt == 1

    stop.set()
    await asyncio.wait_for(task, timeout=15)

    assert blocking_handler.cancelled == [job_id], "task скасовано по вичерпанню grace"
    job_row = await read_job(pg_sessions, job_id)
    assert (job_row.status, job_row.lease_owner) == ("pending", None), "повернуто одразу"
    assert job_row.attempt == 0, "спроба не витрачена планованою зупинкою"
    assert (job_row.last_error_code, job_row.last_error_message) == (None, None)
    async with pg_sessions() as session:
        letters = list(
            (await session.execute(select(DeadLetter).where(DeadLetter.job_id == job_id))).scalars()
        )
    assert letters == [], "dead letter про вичерпані спроби не пишеться: ніхто не провалив job"

    # Job знову claimable і має повний бюджет спроб.
    async with pg_sessions() as session, session.begin():
        claimed = await queue_repo.claim(session, ["fetch"], "next-instance", 60, limit=1)
    assert [(job.job_id, job.attempt) for job in claimed] == [(job_id, 1)]
