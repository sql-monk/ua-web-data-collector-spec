"""Незалежні adversarial-тести WP-01D PR1b (§13 LOGIN-ролі runtime, п.3 і п.5 картки).

Доповнюють `test_runtime_login.py` реалізатора сценаріями, яких там немає:

- права кожної LOGIN-ролі достатні для **повного** циклу worker-а її ролі за мапінгом §13 —
  bootstrap pool (+ audit), реєстрація, claim, heartbeat, complete, retry, quarantine (+ dead
  letter) і release на drain — для всіх `WorkerRole`, зокрема `export` під
  `collector_scheduler` (варіант (а) deps WP-01A→WP-01D §1);
- справжній процес `collector worker|scheduler` (subprocess, а не CliRunner): під superuser-ом,
  членом `collector_migrate` і роллю чужого компонента — exit ≠ 0, без жодного рядка в
  `worker_instances`/`worker_pools` і без DSN/пароля у stdout/stderr навіть на DEBUG;
- rollback `COLLECTOR_WORKER_PLACEHOLDER=1` не ламається навіть із «забороненим» DSN: процес живе
  і до PostgreSQL не підключається;
- drain не повертає **чужий** lease: якщо job уже перехопив інший owner, `_release_leases`
  отримує `LeaseNotOwnedError` і лишає чужий lease недоторканим.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.models import (
    AuditLog,
    CrawlJob,
    DeadLetter,
    WorkerInstance,
    WorkerPool,
)
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.persistence.postgres.roles import MIGRATE_ROLE
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import PermanentTaskError, Task, TaskHandler, TaskResult
from collector.workers.roles import WorkerRole, db_role_for
from collector.workers.runtime import WorkerRuntime

from .conftest import PAST, T0, ControlledHandler, RoleSessions, WaitFor

pytestmark = pytest.mark.integration

MakeConfig = Callable[..., WorkerRuntimeConfig]
MakePool = Callable[..., Awaitable[int]]
CLI_TIMEOUT = 90
SLOW = 60.0
"""Таймаут очікування стану в циклових тестах: на завантаженому хості (паралельні прогони,
сторонні контейнери) event loop буває заблокований на десятки секунд — чекаємо довше, але
все одно на стан, а не на час."""


class CycleHandler(TaskHandler):
    """n=0 → success, n=1 → retry, n=2 → quarantine, n=3 → висить до drain."""

    def __init__(self, job_type: str) -> None:
        self._job_type = job_type
        self.done: list[int] = []
        self.cancelled: list[int] = []
        self.started: list[int] = []

    @property
    def job_types(self) -> tuple[str, ...]:
        return (self._job_type,)

    async def check_ready(self) -> None:
        return None

    async def handle(self, task: Task) -> TaskResult:
        n = int(str(task.args["n"]))
        self.started.append(n)
        try:
            if n == 0:
                return TaskResult.success()
            if n == 1:
                msg = "тимчасова помилка джерела"
                raise RuntimeError(msg)
            if n == 2:
                msg = "непридатний вхід"
                raise PermanentTaskError(msg, error_code="bad_input")
            await asyncio.Event().wait()
            return TaskResult.success()  # pragma: no cover — недосяжно
        except asyncio.CancelledError:
            self.cancelled.append(n)
            raise
        finally:
            if n != 3:
                self.done.append(n)


async def _enqueue(
    sessions: async_sessionmaker[AsyncSession], job_type: str, ns: list[int]
) -> dict[int, UUID]:
    ids: dict[int, UUID] = {}
    async with sessions() as session, session.begin():
        for n in ns:
            job = await queue_repo.enqueue(
                session,
                queue_repo.NewJob(
                    job_type=job_type,
                    idempotency_key=f"cycle:{job_type}:{n}",
                    args={"n": n},
                    max_attempts=5,
                ),
                now=PAST,
            )
            ids[n] = job.job_id
    return ids


async def _job(sessions: async_sessionmaker[AsyncSession], job_id: UUID) -> CrawlJob:
    async with sessions() as session:
        job = await session.get(CrawlJob, job_id)
    assert job is not None
    return job


@pytest.mark.parametrize("role", list(WorkerRole), ids=lambda r: r.value)
async def test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role(
    role: WorkerRole,
    pg_sessions: async_sessionmaker[AsyncSession],
    role_sessions: RoleSessions,
    worker_config: MakeConfig,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Кожна роль worker-а під своєю LOGIN-роллю проходить увесь цикл без permission denied."""
    job_type = f"{role.value}-cycle"
    handler = CycleHandler(job_type)
    ids = await _enqueue(pg_sessions, job_type, [0, 1, 2])
    runtime = WorkerRuntime(
        worker_config(role=role, lease_seconds=180, stop_grace_seconds=0.3),
        role_sessions(role),
        handler,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(runtime.run(stop=stop, install_signals=False))
    running.append(task)

    await wait_for(
        lambda: sorted(handler.done) == [0, 1, 2],
        what=f"{role}: три jobs відпрацьовано",
        timeout=SLOW,
    )
    await wait_for(
        lambda: runtime.active_tasks == 0, what=f"{role}: результати записано", timeout=SLOW
    )
    assert (await _job(pg_sessions, ids[0])).status == "succeeded"
    retried = await _job(pg_sessions, ids[1])
    assert (retried.status, retried.last_error_code) == ("retry", "handler_error")
    quarantined = await _job(pg_sessions, ids[2])
    assert (quarantined.status, quarantined.last_error_code) == ("quarantined", "bad_input")

    ids.update(await _enqueue(pg_sessions, job_type, [3]))
    await wait_for(
        lambda: runtime.active_tasks == 1, what=f"{role}: четверта job у роботі", timeout=SLOW
    )
    beats = runtime.heartbeats
    await wait_for(
        lambda: runtime.heartbeats >= beats + 2, what=f"{role}: heartbeat під lease", timeout=SLOW
    )
    stop.set()
    await asyncio.wait_for(task, timeout=SLOW)

    assert handler.cancelled == [3]
    released = await _job(pg_sessions, ids[3])
    assert (released.status, released.lease_owner, released.attempt) == ("pending", None, 0)
    assert (released.last_error_code, released.last_error_message) == (None, None)
    assert runtime.lost_leases == 0
    assert runtime.fences == 0

    async with pg_sessions() as session:
        pool = await pools_repo.get_pool(session, role)
        instance = await session.get(WorkerInstance, runtime.instance_id)
        letters = await session.scalar(
            select(func.count()).select_from(DeadLetter).where(DeadLetter.job_id == ids[2])
        )
        audits = await session.scalar(select(func.count()).select_from(AuditLog))
    assert pool is not None and pool.updated_by == f"worker:{runtime.instance_id}", "bootstrap"
    assert instance is not None and instance.status == "stopped"
    assert letters == 1, "dead letter карантину записано під runtime-роллю"
    assert audits and audits >= 1, "bootstrap pool пише audit у своїй транзакції"


async def test_drain_never_releases_a_lease_that_another_owner_took_over(
    pg_sessions: async_sessionmaker[AsyncSession],
    runtime_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    make_pool: MakePool,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Lease перехоплено (recovery + claim іншим owner-ом) до першого heartbeat → drain
    отримує `LeaseNotOwnedError` і не чіпає чужий lease/attempt (deps WP-01A→WP-01D §2)."""
    # Один слот: інакше після recovery runtime сам перехопить job назад вільним слотом.
    await make_pool(concurrency=1)
    handler = ControlledHandler(blocking=True)
    async with pg_sessions() as session, session.begin():
        job = await queue_repo.enqueue(
            session,
            queue_repo.NewJob(job_type="fetch", idempotency_key="stolen", args={}),
            now=PAST,
        )
        job_id = job.job_id
    # Heartbeat рідкий: перехоплення має статися ДО того, як runtime сам помітить втрату lease,
    # щоб саме `_release_leases` зустрівся з чужим owner-ом.
    runtime = WorkerRuntime(
        worker_config(lease_seconds=120, heartbeat_seconds=30, stop_grace_seconds=0.2),
        runtime_sessions,
        handler,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(runtime.run(stop=stop, install_signals=False))
    running.append(task)
    await wait_for(lambda: runtime.active_tasks == 1, what="job у роботі")

    async with pg_sessions() as session, session.begin():
        recovered = await queue_repo.recover_expired_leases(
            session, now=utcnow() + timedelta(days=1)
        )
    assert recovered == [job_id]
    async with pg_sessions() as session, session.begin():
        stolen = await queue_repo.claim(session, ["fetch"], "other-instance", 60, now=T0)
    assert [j.job_id for j in stolen] == [job_id]
    before = await _job(pg_sessions, job_id)

    stop.set()
    await asyncio.wait_for(task, timeout=15)

    assert handler.cancelled == [job_id], "task скасовано по grace, тобто drain пішов у release"
    after = await _job(pg_sessions, job_id)
    assert (after.status, after.lease_owner) == ("leased", "other-instance")
    assert after.lease_expires_at == before.lease_expires_at
    assert after.attempt == before.attempt == 2, "release чужого lease не компенсує attempt"
    assert (after.last_error_code, after.last_error_message) == (None, None)


# --- справжній процес CLI ------------------------------------------------------------------


def _run_cli(argv: list[str], url: URL, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COLLECTOR_")}
    env.update(
        {
            "COLLECTOR_POSTGRES_DSN": url.render_as_string(hide_password=False),
            "COLLECTOR_WORKER_PLACEHOLDER": "0",
            "COLLECTOR_WORKER_STOP_GRACE_SECONDS": "1",
            "COLLECTOR_LOG_LEVEL": "DEBUG",
            "PYTHONIOENCODING": "utf-8",
            **extra,
        }
    )
    return subprocess.run(  # noqa: S603 — фіксований argv, свій інтерпретатор
        [sys.executable, "-m", "collector.cli", *argv],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLI_TIMEOUT,
        check=False,
    )


def _assert_no_secret(output: str, url: URL) -> None:
    dsn = url.render_as_string(hide_password=False)
    assert dsn not in output, "DSN у виводі процесу"
    if url.password:
        assert f":{url.password}@" not in output
        if len(str(url.password)) >= 8:
            assert str(url.password) not in output, "пароль у виводі процесу"


async def _assert_db_untouched(pg_sessions: async_sessionmaker[AsyncSession]) -> None:
    async with pg_sessions() as session:
        instances = await session.scalar(select(func.count()).select_from(WorkerInstance))
        pools = await session.scalar(select(func.count()).select_from(WorkerPool))
        leased = await session.scalar(
            select(func.count()).select_from(CrawlJob).where(CrawlJob.status != "pending")
        )
    assert (instances, pools, leased) == (0, 0, 0), "до першого запису/claim справа не дійшла"


async def _seed_job(pg_sessions: async_sessionmaker[AsyncSession]) -> None:
    async with pg_sessions() as session, session.begin():
        await queue_repo.enqueue(
            session,
            queue_repo.NewJob(job_type="fetch", idempotency_key="cli-seed", args={}),
            now=PAST,
        )


@pytest.mark.parametrize("argv", [["worker", "fetch"], ["worker", "export"], ["scheduler"]])
async def test_process_exits_nonzero_as_superuser_before_any_write(
    argv: list[str],
    pg_database: PostgresSettings,
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_job(pg_sessions)
    result = await asyncio.to_thread(_run_cli, argv, pg_database.url)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "role login:" in result.stderr, output
    assert "superuser" in result.stderr
    assert "Traceback" not in output
    _assert_no_secret(output, pg_database.url)
    await _assert_db_untouched(pg_sessions)


@pytest.fixture
async def fetcher_in_migrate(
    login_urls: dict[str, URL], pg_engine: AsyncEngine
) -> AsyncIterator[dict[str, URL]]:
    async with pg_engine.begin() as conn:
        await conn.execute(text(f'GRANT "{MIGRATE_ROLE}" TO collector_fetcher'))
    try:
        yield login_urls
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text(f'REVOKE "{MIGRATE_ROLE}" FROM collector_fetcher'))


async def test_process_exits_nonzero_as_member_of_collector_migrate(
    fetcher_in_migrate: dict[str, URL],
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_job(pg_sessions)
    url = fetcher_in_migrate["collector_fetcher"]
    result = await asyncio.to_thread(_run_cli, ["worker", "fetch"], url)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "role login:" in result.stderr and MIGRATE_ROLE in result.stderr, output
    assert "Traceback" not in output
    _assert_no_secret(output, url)
    await _assert_db_untouched(pg_sessions)


@pytest.mark.parametrize(
    ("argv", "wrong_role"),
    [
        (["worker", "fetch"], "collector_projector"),
        (["worker", "parse"], "collector_fetcher"),
        (["worker", "maintenance"], "collector_fetcher"),
        (["worker", "export"], "collector_export_ro"),
        (["worker", "translation"], "collector_api_ro"),
        (["scheduler"], "collector_fetcher"),
    ],
)
async def test_process_exits_nonzero_with_a_login_role_that_does_not_match_its_worker_role(
    argv: list[str],
    wrong_role: str,
    login_urls: dict[str, URL],
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_job(pg_sessions)
    url = login_urls[wrong_role]
    result = await asyncio.to_thread(_run_cli, argv, url)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "role login:" in result.stderr, output
    expected = "collector_scheduler" if argv == ["scheduler"] else db_role_for(WorkerRole(argv[1]))
    assert expected in result.stderr, output
    assert "Traceback" not in output
    _assert_no_secret(output, url)
    await _assert_db_untouched(pg_sessions)


@pytest.mark.parametrize("argv", [["worker", "fetch"], ["scheduler"]])
async def test_placeholder_rollback_ignores_the_database_even_with_a_refused_dsn(
    argv: list[str],
    pg_database: PostgresSettings,
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`COLLECTOR_WORKER_PLACEHOLDER=1` — rollback WP-00: перевірка ролі не запускається,
    процес живий (placeholder), до PostgreSQL не підключається і нічого не пише."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("COLLECTOR_")}
    env.update(
        {
            "COLLECTOR_POSTGRES_DSN": pg_database.url.render_as_string(hide_password=False),
            "COLLECTOR_WORKER_PLACEHOLDER": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.Popen(  # noqa: S603 — фіксований argv, свій інтерпретатор
        [sys.executable, "-m", "collector.cli", *argv],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert proc.stderr is not None
        first = await asyncio.wait_for(asyncio.to_thread(proc.stderr.readline), timeout=60)
        assert first.strip() == "not implemented: owned by WP-01D", first
        started = await asyncio.wait_for(asyncio.to_thread(proc.stderr.readline), timeout=60)
        assert "placeholder.started" in started, started
        assert proc.poll() is None, "placeholder живий, не exit 1 через роль"
        async with pg_sessions() as session:
            conns = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name LIKE 'collector-%'"
                )
            )
        assert conns == 0, "placeholder не підключається до PostgreSQL"
        await _assert_db_untouched(pg_sessions)
    finally:
        proc.kill()
        proc.communicate(timeout=30)
