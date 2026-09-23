"""Worker runtime проти реального PostgreSQL (WP-01D PR1; §7.6, FR-031/FR-032, R-52, R-57).

Сценарії картки: killed replica → lease recovery іншим instance; drain під активним task;
гаряча зміна concurrency; heartbeat не продовжує чужий lease. Плюс реєстрація/readiness,
drain-timeout і role-wide drain barrier.

Кожна перевірка — за станом у БД або лічильниками runtime, не за таймінгом: «прострочений
lease» моделюється явним `now` у `recover_expired_leases`, а не очікуванням.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.errors import LeaseNotOwnedError
from collector.persistence.postgres.models import CrawlJob, WorkerInstance
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.roles import WorkerRole
from collector.workers.runtime import WorkerRuntime

from .conftest import T0, ControlledHandler, WaitFor

pytestmark = pytest.mark.integration

MakePool = Callable[..., Awaitable[int]]
EnqueueJobs = Callable[..., Awaitable[list[UUID]]]
MakeConfig = Callable[..., WorkerRuntimeConfig]

FENCE_WINDOW = 1.5
"""Вікно self-fencing у fencing-тестах: коротке для швидкого тесту, але помітно довше за паузу
event loop-у на завантаженій машині — інакше fence спрацьовує на здоровій базі."""


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


def far_future() -> object:
    """Момент, у який будь-який виданий у тесті lease уже прострочений."""
    return utcnow() + timedelta(days=1)


async def test_instance_registers_becomes_ready_and_stops_on_drain(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    revision = await make_pool(concurrency=2)
    runtime = WorkerRuntime(worker_config(), pg_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)

    await wait_for(lambda: runtime.status == "ready", what="instance у статусі ready")
    instance = await read_instance(pg_sessions, runtime.instance_id)
    assert instance.status == "ready"
    assert instance.role == WorkerRole.FETCH.value
    assert (instance.hostname, instance.deployment) == ("pytest-host", "pytest")
    assert instance.container_id == "pytest-container"
    assert instance.version
    assert instance.slots_total == 2, "slots_total = desired_concurrency pool-а"
    assert instance.pool_revision == revision
    assert handler.ready_checks == 1, "readiness-перевірка перед переходом у ready"
    assert runtime.instance_id.version == 7, "boot UUIDv7 (§7.6)"

    await wait_for(lambda: runtime.heartbeats >= 2, what="heartbeat-и йдуть")
    stop.set()
    await asyncio.wait_for(task, timeout=15)

    instance = await read_instance(pg_sessions, runtime.instance_id)
    assert instance.status == "stopped"
    assert instance.stopped_at is not None
    assert (instance.slots_active, instance.active_leases) == (0, 0)


async def test_bootstraps_missing_pool_from_spec_defaults(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """На чистій БД pool ролі створюється з defaults §7.6 (інакше FK instance → pool впаде)."""
    runtime = WorkerRuntime(worker_config(role=WorkerRole.EXPORT), pg_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.status == "ready", what="ready на чистій БД")

    async with pg_sessions() as session:
        pool = await pools_repo.get_pool(session, WorkerRole.EXPORT)
    assert pool is not None
    assert (pool.desired_replicas, pool.desired_concurrency) == (1, 2)  # §7.6: export 1 × 2
    assert pool.updated_by == f"worker:{runtime.instance_id}"
    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_killed_replica_lease_is_recovered_and_finished_by_another_instance(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """SIGKILL — fault case: lease не повертається сам, його підбирає recover_expired_leases."""
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)

    killed = WorkerRuntime(worker_config(), pg_sessions, blocking_handler)
    killed_task = start(killed, running, asyncio.Event())
    await wait_for(lambda: bool(blocking_handler.started), what="перший instance узяв job")

    killed_task.cancel()  # kill -9: ні drain, ні повернення lease
    with pytest.raises(asyncio.CancelledError):
        await killed_task

    job = await read_job(pg_sessions, job_id)
    assert job.status == "leased"
    assert job.lease_owner == killed.owner
    assert job.attempt == 1

    async with pg_sessions() as session, session.begin():
        recovered = await queue_repo.recover_expired_leases(session, now=far_future())
    assert recovered == [job_id]

    survivor = WorkerRuntime(worker_config(), pg_sessions, handler)
    stop = asyncio.Event()
    survivor_task = start(survivor, running, stop)
    await wait_for(lambda: job_id in handler.finished, what="job підхопив інший instance")
    stop.set()
    await asyncio.wait_for(survivor_task, timeout=15)

    job = await read_job(pg_sessions, job_id)
    assert job.status == "succeeded"
    assert job.lease_owner is None
    assert job.attempt == 2, "attempt зберігається між recovery і новим claim"


async def test_drain_finishes_active_task_and_takes_no_new_jobs(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    await make_pool(concurrency=1)
    job_ids = await enqueue_jobs(2)
    runtime = WorkerRuntime(worker_config(), pg_sessions, blocking_handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="task у роботі")

    stop.set()
    await wait_for(lambda: runtime.status == "draining", what="перехід у draining")
    assert not runtime.claiming
    statuses = [(await read_job(pg_sessions, job_id)).status for job_id in job_ids]
    assert sorted(statuses) == ["leased", "pending"], "під drain нові jobs не беруться"

    blocking_handler.release.set()
    await asyncio.wait_for(task, timeout=15)

    finished, untouched = (
        (job_ids[0], job_ids[1])
        if job_ids[0] in blocking_handler.started
        else (job_ids[1], job_ids[0])
    )
    assert (await read_job(pg_sessions, finished)).status == "succeeded"
    assert (await read_job(pg_sessions, untouched)).status == "pending"
    assert runtime.lost_leases == 0, "lease не втрачено під час drain"
    assert (await read_instance(pg_sessions, runtime.instance_id)).status == "stopped"


async def test_drain_timeout_returns_the_lease_to_the_queue(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Task, що не вклався у stop_grace_period, скасовується, а його lease повертається."""
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    runtime = WorkerRuntime(worker_config(stop_grace_seconds=0.2), pg_sessions, blocking_handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="task у роботі")

    stop.set()
    await asyncio.wait_for(task, timeout=15)

    assert blocking_handler.cancelled == [job_id]
    job = await read_job(pg_sessions, job_id)
    assert job.status == "retry"
    assert job.lease_owner is None
    assert job.last_error_code == "drain_timeout"
    assert job.not_before <= utcnow(), "job claimable одразу, без штрафного backoff"

    async with pg_sessions() as session, session.begin():
        claimed = await queue_repo.claim(session, ["fetch"], "next-instance", 60, limit=1)
    assert [job.job_id for job in claimed] == [job_id]


async def test_concurrency_hot_change_opens_and_closes_slots_without_restart(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    revision = await make_pool(concurrency=1, max_replicas=4)
    await enqueue_jobs(6)
    runtime = WorkerRuntime(worker_config(), pg_sessions, blocking_handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="один слот за desired_concurrency=1")

    async def set_concurrency(value: int, expected_revision: int) -> int:
        async with pg_sessions() as session, session.begin():
            pool = await pools_repo.upsert_pool(
                session,
                WorkerRole.FETCH,
                pools_repo.PoolDesiredState(
                    desired_replicas=1, desired_concurrency=value, max_replicas=4
                ),
                actor="operator",
                reason="hot change",
                expected_revision=expected_revision,
                now=T0,
            )
            return pool.revision

    revision = await set_concurrency(3, revision)
    await wait_for(lambda: runtime.active_tasks == 3, what="нові слоти відкрилися одразу")
    assert runtime.desired_concurrency == 3
    assert (await read_instance(pg_sessions, runtime.instance_id)).slots_total == 3

    revision = await set_concurrency(1, revision)
    await wait_for(lambda: runtime.desired_concurrency == 1, what="зменшення прочитано")
    assert runtime.active_tasks == 3, "активні tasks не скасовуються, а дороблюються"

    blocking_handler.release.set()
    await wait_for(lambda: len(blocking_handler.finished) == 3, what="три активні task завершено")
    blocking_handler.release.clear()

    await wait_for(lambda: runtime.active_tasks == 1, what="після завершення лишається 1 слот")
    beats = runtime.heartbeats
    await wait_for(lambda: runtime.heartbeats >= beats + 3, what="кілька heartbeat-ів поспіль")
    assert runtime.active_tasks == 1, "зайві слоти закрито; понад desired не claim-имо"

    blocking_handler.release.set()
    stop.set()
    await asyncio.wait_for(task, timeout=15)
    assert runtime.pool_revision == revision


async def test_heartbeat_does_not_extend_a_foreign_lease(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Після recovery+claim іншим owner-ом heartbeat старого власника нічого не продовжує."""
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    runtime = WorkerRuntime(worker_config(), pg_sessions, blocking_handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="job у роботі першого instance")

    async with pg_sessions() as session, session.begin():
        assert await queue_repo.recover_expired_leases(session, now=far_future()) == [job_id]
    async with pg_sessions() as session, session.begin():
        stolen = await queue_repo.claim(session, ["fetch"], "other-instance", 60, now=T0)
    assert [job.job_id for job in stolen] == [job_id]
    expected_expiry = T0 + timedelta(seconds=60)

    await wait_for(lambda: runtime.lost_leases >= 1, what="heartbeat помітив чужий lease")
    await wait_for(lambda: blocking_handler.cancelled == [job_id], what="локальний task скасовано")
    job = await read_job(pg_sessions, job_id)
    assert job.lease_owner == "other-instance"
    assert job.lease_expires_at == expected_expiry, "чужий lease не продовжено"

    # Прямий контракт репозиторію: heartbeat не власника — помилка, а не мовчазне продовження.
    async with pg_sessions() as session, session.begin():
        with pytest.raises(LeaseNotOwnedError):
            await queue_repo.heartbeat(session, job_id, runtime.owner, 60)
    assert (await read_job(pg_sessions, job_id)).lease_expires_at == expected_expiry

    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_role_wide_drain_barrier_stops_claim_without_sigterm(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """R-57: claim зупиняє `drain_requested_at` у рядку instance, а не вибір контейнера."""
    await make_pool(concurrency=2)
    runtime = WorkerRuntime(worker_config(), pg_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.status == "ready", what="ready")

    async with pg_sessions() as session, session.begin():
        await pools_repo.mark_draining(session, runtime.instance_id)
    await wait_for(lambda: not runtime.claiming, what="барʼєр drain зупинив claim")

    # Claim, що вже був у базі в момент барʼєра, job-и, покладеної ПІСЛЯ коміту барʼєра, не
    # бачить (`FOR SHARE` на рядку instance — окремий тест
    # `test_claim_in_flight_never_takes_a_job_enqueued_after_the_drain_barrier`). Контролер
    # PR3 однаково чекає на `active_leases = 0`: jobs, узяті ДО барʼєра, дороблюються.
    beats = runtime.heartbeats
    await wait_for(lambda: runtime.heartbeats >= beats + 2, what="цикл claim стоїть під барʼєром")
    assert runtime.active_tasks == 0

    (job_id,) = await enqueue_jobs(1)
    beats = runtime.heartbeats
    await wait_for(lambda: runtime.heartbeats >= beats + 3, what="кілька heartbeat-ів під барʼєром")
    assert (await read_job(pg_sessions, job_id)).status == "pending"
    assert not handler.started

    async with pg_sessions() as session, session.begin():
        await pools_repo.mark_ready(session, runtime.instance_id)
    await wait_for(lambda: job_id in handler.finished, what="після зняття барʼєра job виконано")

    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_handler_failure_becomes_a_retry_with_backoff(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    handler.raise_error = RuntimeError("джерело віддало сміття")
    runtime = WorkerRuntime(worker_config(), pg_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)

    await wait_for(
        lambda: job_id in handler.finished, what="handler відпрацював (з винятком)", timeout=15
    )
    await wait_for(lambda: runtime.active_tasks == 0, what="runtime відзвітував про результат")
    job = await read_job(pg_sessions, job_id)
    assert job.status == "retry"
    assert job.last_error_code == "handler_error"
    assert job.not_before > utcnow(), "retry отримує backoff"
    assert job.lease_owner is None

    stop.set()
    await asyncio.wait_for(task, timeout=15)


class FlakySessions:
    """Проксі над `async_sessionmaker`, який на команду тесту імітує недоступність PostgreSQL.

    Рівно те, що бачить runtime при падінні бази: будь-яка спроба відкрити session закінчується
    помилкою драйвера. Детерміновано і без зупинки контейнера — тест керує «відмовою» прапорцем.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self.failing = False
        self.failures = 0

    def __call__(self) -> AsyncSession:
        if self.failing:
            self.failures += 1
            raise OperationalError(
                "SELECT 1", None, ConnectionRefusedError("postgres unreachable (test)")
            )
        return self._sessions()


async def test_self_fencing_cancels_active_tasks_when_the_database_stops_confirming_the_lease(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """F3: недоступна база довше за `fence_after` → instance сам скасовує роботу (§7.6, §9.3).

    Без self-fencing lease мовчки спливає, `recover_expired_leases` віддає job іншому instance,
    а цей продовжує її виконувати — подвійна обробка для доменних handler-ів. Після фікса
    runtime скасовує активні tasks, не звітує за ними `complete` і не бере нових, поки база не
    підтвердить heartbeat.
    """
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    sessions = FlakySessions(pg_sessions)
    runtime = WorkerRuntime(
        # Явне вікно fencing замість половини lease TTL — щоб тест не чекав десятки секунд.
        # Воно має з запасом перекривати паузу event loop-у на завантаженій машині (під
        # CPU-навантаженням спостерігались паузи ~0.85 с): 0.2 с давало хибний fence ще до
        # «відмови» бази і флейк `assert not runtime.fenced` (звіт flaky-scaling-tests).
        worker_config(lease_seconds=6, heartbeat_seconds=0.05, fence_after_seconds=FENCE_WINDOW),
        cast("async_sessionmaker[AsyncSession]", sessions),
        blocking_handler,
    )
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="task у роботі")
    assert not runtime.fenced

    sessions.failing = True
    await wait_for(lambda: runtime.fenced, what="self-fencing після втрати підтвердження lease")
    await wait_for(lambda: blocking_handler.cancelled == [job_id], what="активний task скасовано")
    assert runtime.active_tasks == 0
    assert not runtime.claiming, "поки lease не підтверджено, нові jobs не беруться"
    assert runtime.fences == 1

    job = await read_job(pg_sessions, job_id)
    assert job.status == "leased", "жодного тихого complete — скасована task не звітує"
    assert job.lease_owner == runtime.owner

    sessions.failing = False
    await wait_for(lambda: not runtime.fenced, what="підтверджений heartbeat знімає fencing")
    await wait_for(lambda: runtime.claiming, what="claim відновлено")

    stop.set()
    await asyncio.wait_for(task, timeout=15)
    assert (await read_instance(pg_sessions, runtime.instance_id)).status == "stopped"


class HangingSessions:
    """Сесії, які **зависають без винятку** — типова форма відмови PostgreSQL.

    Мережевий поділ або failover не дають ні `ConnectionRefusedError`, ні RST: запит просто не
    повертається. Саме цей сценарій був сліпою зоною подієвого fencing (H-1 код-рев'ю), і саме
    його відтворює ця фабрика: `__aenter__` спить «вічно».
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self.hanging = False
        self.hangs = 0

    def __call__(self) -> AsyncSession:
        if not self.hanging:
            return self._sessions()
        self.hangs += 1
        return cast("AsyncSession", _HangingSession())


class _HangingSession:
    async def __aenter__(self) -> AsyncSession:
        await asyncio.sleep(3600)
        raise AssertionError("недосяжно")  # pragma: no cover

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_self_fencing_fires_when_the_database_hangs_without_raising(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """H-1: fencing керується ЧАСОМ від останнього підтвердженого heartbeat, а не винятком.

    Сторож lease — окрема задача, тому спрацьовує навіть тоді, коли heartbeat-корутина висить
    у драйвері й ніколи не повертається. Перевіряємо: fence у межах вікна, активні tasks
    скасовані, `complete` не звітується, claim зупинено.
    """
    await make_pool(concurrency=1)
    (job_id,) = await enqueue_jobs(1)
    sessions = HangingSessions(pg_sessions)
    config = worker_config(
        lease_seconds=9, heartbeat_seconds=0.05, fence_after_seconds=FENCE_WINDOW
    )
    runtime = WorkerRuntime(
        config, cast("async_sessionmaker[AsyncSession]", sessions), blocking_handler
    )
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="task у роботі")

    started = monotonic()
    sessions.hanging = True
    await wait_for(lambda: runtime.fenced, what="сторож lease спрацював на зависанні бази")
    elapsed = monotonic() - started
    assert elapsed < config.lease_seconds, (
        f"fence має спрацювати в межах lease TTL, минуло {elapsed:.2f} с"
    )
    assert sessions.hangs >= 1, "heartbeat справді зависав, а не падав із винятком"
    # Лічильник читаємо лише після двох зависань поспіль: до цього моменту міг ще
    # добігати тік, який відкрив свою session ДО перемикання прапорця.
    await wait_for(lambda: sessions.hangs >= 2, what="другий тік теж зависає")
    frozen = runtime.heartbeats
    # Кожен завислий тік живе `heartbeat_tick_budget` (2 × fence_after), тож ще один тік — це
    # вже секунди без підтвердження; більше не додає доказовості, лише часу.
    await wait_for(lambda: sessions.hangs >= 3, what="ще один тік зависає")
    assert runtime.heartbeats == frozen, "поки база висить, підтверджених heartbeat немає"

    await wait_for(lambda: blocking_handler.cancelled == [job_id], what="активний task скасовано")
    assert runtime.active_tasks == 0
    assert not runtime.claiming
    job = await read_job(pg_sessions, job_id)
    assert job.status == "leased", "жодного тихого complete від скасованої task"

    sessions.hanging = False
    await wait_for(lambda: not runtime.fenced, what="після підтвердженого heartbeat fence знято")
    stop.set()
    await asyncio.wait_for(task, timeout=20)


async def test_claim_in_flight_never_takes_a_job_enqueued_after_the_drain_barrier(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-57: коміт `mark_draining` = жодного claim цього instance, який барʼєра не бачив.

    Детермінована форма флейку під навантаженням: claim-loop вирішив claim-ити (барʼєра ще
    немає), і поки його транзакція в базі, контролер ставить барʼєр, а в чергу падає job.
    До фікса claim брав цю job; тепер `FOR SHARE` на рядку instance змушує `mark_draining`
    чекати коміту claim, тож job, покладена після барʼєра, claim-у вже не видно.
    """
    await make_pool(concurrency=1)
    runtime = WorkerRuntime(worker_config(), pg_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.status == "ready", what="ready")

    original_claim = queue_repo.claim
    side: list[asyncio.Task[UUID]] = []

    async def barrier_then_enqueue() -> UUID:
        async with pg_sessions() as session, session.begin():
            await pools_repo.mark_draining(session, runtime.instance_id)
        (job_id,) = await enqueue_jobs(1, prefix="after-barrier")
        return job_id

    async def claim_racing_with_the_barrier(*args: object, **kwargs: object) -> object:
        if not side:
            side.append(asyncio.create_task(barrier_then_enqueue()))
            # Без фікса барʼєр і job комітяться тут-таки; з фіксом `mark_draining` чекає на
            # рядку instance до коміту цього claim — таймаут і є очікуваний шлях.
            await asyncio.wait(side, timeout=1.0)
        return await original_claim(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(queue_repo, "claim", claim_racing_with_the_barrier)
    await wait_for(lambda: bool(side) and side[0].done(), what="барʼєр і job закомічено")
    job_id = side[0].result()
    await wait_for(lambda: not runtime.claiming, what="барʼєр застосовано")
    beats = runtime.heartbeats
    await wait_for(lambda: runtime.heartbeats >= beats + 3, what="кілька heartbeat-ів під барʼєром")

    assert handler.started == [], "claim, що був у базі під час барʼєра, не взяв нову job"
    assert (await read_job(pg_sessions, job_id)).status == "pending"
    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_jobs_claimed_while_the_fence_went_up_are_not_started(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim, що повернувся вже під fence, не запускає tasks (сторож їх більше не скасує)."""
    await make_pool(concurrency=1)
    sessions = FlakySessions(pg_sessions)
    runtime = WorkerRuntime(
        worker_config(lease_seconds=6, heartbeat_seconds=0.05, fence_after_seconds=FENCE_WINDOW),
        cast("async_sessionmaker[AsyncSession]", sessions),
        handler,
    )
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.status == "ready", what="ready")

    original_claim = queue_repo.claim
    claimed: list[UUID] = []

    async def claim_then_lose_the_database(*args: object, **kwargs: object) -> object:
        jobs = await original_claim(*args, **kwargs)  # type: ignore[arg-type]
        if jobs and not claimed:
            claimed.extend(job.job_id for job in jobs)
            # Session цього claim уже відкрита; heartbeat-и відтепер падають, і claim
            # повертається лише після того, як сторож підняв fence.
            sessions.failing = True
            await wait_for(lambda: runtime.fenced, what="fence під час claim")
        return jobs

    monkeypatch.setattr(queue_repo, "claim", claim_then_lose_the_database)
    (job_id,) = await enqueue_jobs(1)
    await wait_for(lambda: claimed == [job_id], what="claim повернув job")
    await wait_for(lambda: runtime.lost_leases >= 1, what="claimed-під-fence job відкладено")

    assert handler.started == [], "task без підтвердженого lease не стартує"
    assert runtime.active_tasks == 0
    job = await read_job(pg_sessions, job_id)
    assert (job.status, job.lease_owner) == ("leased", runtime.owner), (
        "lease лишається спливати; job поверне recover_expired_leases"
    )

    sessions.failing = False
    await wait_for(lambda: not runtime.fenced, what="fence знято")
    stop.set()
    await asyncio.wait_for(task, timeout=15)


async def test_hot_change_above_the_connection_ceiling_is_clamped(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    blocking_handler: ControlledHandler,
    make_pool: MakePool,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """M-3: `desired_concurrency` понад стелю процесу обрізається, а не топить pool з'єднань."""
    revision = await make_pool(concurrency=1, max_replicas=4)
    await enqueue_jobs(6)
    runtime = WorkerRuntime(worker_config(max_concurrency=2), pg_sessions, blocking_handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)
    await wait_for(lambda: runtime.active_tasks == 1, what="один слот")

    async with pg_sessions() as session, session.begin():
        await pools_repo.upsert_pool(
            session,
            WorkerRole.FETCH,
            pools_repo.PoolDesiredState(desired_replicas=1, desired_concurrency=8, max_replicas=4),
            actor="operator",
            reason="hot change above the ceiling",
            expected_revision=revision,
            now=T0,
        )
    await wait_for(lambda: runtime.desired_concurrency == 8, what="нове desired прочитано")
    await wait_for(lambda: runtime.active_tasks == 2, what="відкрито рівно стелю слотів")

    beats = runtime.heartbeats
    await wait_for(lambda: runtime.heartbeats >= beats + 3, what="кілька heartbeat-ів поспіль")
    assert runtime.active_tasks == 2, "понад стелю процес слотів не відкриває"
    assert runtime.effective_concurrency == 2
    instance = await read_instance(pg_sessions, runtime.instance_id)
    assert instance.slots_total == 2, "heartbeat-derived capacity не обіцяє більше, ніж є"

    blocking_handler.release.set()
    stop.set()
    await asyncio.wait_for(task, timeout=20)


async def test_boot_fails_loudly_when_the_ready_transition_cannot_be_written(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
) -> None:
    """M-1: worker не лишається «живим, але німим» — boot падає, і оркестратор перезапускає."""
    from collector.persistence.postgres.errors import NotFoundError
    from collector.workers import runtime as runtime_module

    await make_pool(concurrency=1)
    runtime = WorkerRuntime(worker_config(), pg_sessions, handler)
    attempts = 0

    async def failing_status(*_args: object, **_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise NotFoundError("instance зник між реєстрацією і переходом у ready")

    original = runtime_module.pools_repo.set_instance_status
    runtime_module.pools_repo.set_instance_status = failing_status  # type: ignore[assignment]
    try:
        with pytest.raises(runtime_module.WorkerRuntimeError, match="ready"):
            await runtime.run(stop=asyncio.Event(), install_signals=False)
    finally:
        runtime_module.pools_repo.set_instance_status = original  # type: ignore[assignment]

    assert attempts == runtime_module.READY_RETRY_ATTEMPTS, "перехід повторювався з backoff"
    assert runtime.status == "starting"
    instance = await read_instance(pg_sessions, runtime.instance_id)
    assert instance.status == "starting", "рядок лишається starting і застаріє за heartbeat TTL"


async def test_liveness_marker_is_refreshed_by_the_loop_and_removed_on_stop(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    make_pool: MakePool,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
    tmp_path: Path,
) -> None:
    """Вимога 7 картки: healthcheck читає mtime маркера, який оновлює живий цикл процесу."""
    await make_pool(concurrency=1)
    marker = tmp_path / "runtime.alive"
    runtime = WorkerRuntime(worker_config(liveness_path=marker), pg_sessions, handler)
    stop = asyncio.Event()
    task = start(runtime, running, stop)

    await wait_for(lambda: marker.is_file(), what="маркер зʼявився після реєстрації")
    # «Постаріла» позначка має оновитись сторожем без жодного запиту в БД.
    stale_ns = marker.stat().st_mtime_ns - 5_000_000_000
    os.utime(marker, ns=(stale_ns, stale_ns))
    await wait_for(lambda: marker.stat().st_mtime_ns > stale_ns, what="сторож оновив mtime маркера")
    assert runtime.liveness.refreshes >= 2

    stop.set()
    await asyncio.wait_for(task, timeout=15)
    assert not marker.exists(), "на штатному виході маркер прибирається"
