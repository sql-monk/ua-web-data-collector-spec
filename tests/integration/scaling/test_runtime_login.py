"""Runtime стартує лише під власною LOGIN-роллю §13 (картка WP-01D PR1b п.3; ризик I-1).

- під superuser-ом (спільний міграційний DSN, знахідка F1) worker і scheduler не стартують:
  `RoleLoginError` до реєстрації instance, bootstrap pool і першого claim;
- під runtime-роллю, яка є членом `collector_migrate`, — теж ні;
- під роллю чужого компонента (fetch-worker з DSN parser-а) — теж ні;
- під `collector_fetcher` worker стартує, claim-ить і завершує job, а в `pg_stat_activity` його
  з'єднання — не superuser (acceptance картки, рівень БД);
- CLI `collector worker`/`scheduler` на відмові — exit 1, зрозумілий stderr без DSN/пароля.

Паролі ролей одноразові, генеруються фікстурою `login_urls` у рантаймі.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from collector.cli import app
from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.models import CrawlJob, WorkerInstance, WorkerPool
from collector.persistence.postgres.roles import MIGRATE_ROLE, RoleLoginError
from collector.workers.config import SchedulerRuntimeConfig, WorkerRuntimeConfig
from collector.workers.roles import WorkerRole
from collector.workers.runtime import WorkerRuntime
from collector.workers.scheduler import SchedulerRuntime

from .conftest import ControlledHandler, RoleEngine, RoleSessions, WaitFor

pytestmark = pytest.mark.integration

MakeConfig = Callable[..., WorkerRuntimeConfig]
EnqueueJobs = Callable[..., Awaitable[list[UUID]]]
FAST = SchedulerRuntimeConfig(
    lease_name="scheduler-login-test", tick_seconds=0.02, lease_retry_seconds=0.02
)
runner = CliRunner()


async def _count(sessions: async_sessionmaker[AsyncSession], model: type) -> int:
    async with sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _assert_refused_before_any_write(
    runtime: WorkerRuntime, pg_sessions: async_sessionmaker[AsyncSession], match: str
) -> None:
    with pytest.raises(RoleLoginError, match=match):
        await asyncio.wait_for(runtime.run(install_signals=False), timeout=15)
    assert await _count(pg_sessions, WorkerInstance) == 0, "instance не зареєстровано"
    assert await _count(pg_sessions, WorkerPool) == 0, "bootstrap pool не виконано"
    async with pg_sessions() as session:
        leased = await session.scalar(
            select(func.count()).select_from(CrawlJob).where(CrawlJob.status != "pending")
        )
    assert leased == 0, "жодного claim"


async def test_worker_refuses_to_start_as_superuser(
    pg_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    enqueue_jobs: EnqueueJobs,
) -> None:
    """Спільний міграційний DSN (superuser) → відмова до першого запису (знахідка F1)."""
    await enqueue_jobs(1)
    runtime = WorkerRuntime(worker_config(), pg_sessions, handler)
    await _assert_refused_before_any_write(runtime, pg_sessions, "superuser")
    assert handler.ready_checks == 0


@pytest.fixture
async def fetcher_in_migrate(
    login_urls: dict[str, URL], pg_engine: AsyncEngine
) -> AsyncIterator[None]:
    """`collector_fetcher` тимчасово — член `collector_migrate` (ручна помилка оператора)."""
    async with pg_engine.begin() as conn:
        await conn.execute(text(f'GRANT "{MIGRATE_ROLE}" TO collector_fetcher'))
    try:
        yield
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text(f'REVOKE "{MIGRATE_ROLE}" FROM collector_fetcher'))


@pytest.mark.usefixtures("fetcher_in_migrate")
async def test_worker_refuses_to_start_as_a_member_of_collector_migrate(
    pg_sessions: async_sessionmaker[AsyncSession],
    runtime_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    handler: ControlledHandler,
    enqueue_jobs: EnqueueJobs,
) -> None:
    await enqueue_jobs(1)
    runtime = WorkerRuntime(worker_config(), runtime_sessions, handler)
    await _assert_refused_before_any_write(runtime, pg_sessions, MIGRATE_ROLE)


async def test_worker_refuses_the_login_role_of_another_component(
    pg_sessions: async_sessionmaker[AsyncSession],
    role_sessions: RoleSessions,
    worker_config: MakeConfig,
    handler: ControlledHandler,
    enqueue_jobs: EnqueueJobs,
) -> None:
    """fetch-worker, якому змонтували DSN parser-а, не стартує (§13: лише свій DSN)."""
    await enqueue_jobs(1)
    runtime = WorkerRuntime(worker_config(), role_sessions(WorkerRole.PARSE), handler)
    await _assert_refused_before_any_write(runtime, pg_sessions, "collector_fetcher")


async def test_worker_starts_and_claims_as_collector_fetcher_without_superuser(
    pg_sessions: async_sessionmaker[AsyncSession],
    runtime_sessions: async_sessionmaker[AsyncSession],
    worker_config: MakeConfig,
    enqueue_jobs: EnqueueJobs,
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Під власною роллю: bootstrap pool, реєстрація, claim, complete; з'єднання — не superuser."""
    handler = ControlledHandler(blocking=True)
    (job_id,) = await enqueue_jobs(1)
    runtime = WorkerRuntime(worker_config(), runtime_sessions, handler)
    stop = asyncio.Event()
    task = asyncio.create_task(runtime.run(stop=stop, install_signals=False))
    running.append(task)
    await wait_for(lambda: runtime.active_tasks == 1, what="job claim-нуто під collector_fetcher")

    async with pg_sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT a.usename, u.usesuper FROM pg_stat_activity a "
                    "JOIN pg_user u ON u.usename = a.usename "
                    "WHERE a.datname = current_database() AND a.application_name = :app"
                ),
                {"app": "pytest-collector_fetcher"},
            )
        ).all()
    assert rows, "runtime тримає з'єднання під своєю роллю"
    assert {(row.usename, row.usesuper) for row in rows} == {("collector_fetcher", False)}

    handler.release.set()
    await wait_for(lambda: job_id in handler.finished, what="job завершено")
    stop.set()
    await asyncio.wait_for(task, timeout=15)
    async with pg_sessions() as session:
        job = await session.get(CrawlJob, job_id)
        instance = await session.get(WorkerInstance, runtime.instance_id)
    assert job is not None and job.status == "succeeded"
    assert instance is not None and instance.status == "stopped"


async def test_scheduler_refuses_superuser_and_foreign_role(
    pg_engine: AsyncEngine,
    pg_sessions: async_sessionmaker[AsyncSession],
    role_engine: RoleEngine,
    role_sessions: RoleSessions,
) -> None:
    ticks: list[datetime] = []

    async def tick(_session: AsyncSession, now: datetime) -> None:
        ticks.append(now)

    superuser = SchedulerRuntime(FAST, pg_engine, pg_sessions, tick=tick)
    with pytest.raises(RoleLoginError, match="superuser"):
        await asyncio.wait_for(superuser.run(install_signals=False), timeout=15)
    assert superuser.acquire_attempts == 0, "до advisory lease справа не дійшла"

    fetcher = SchedulerRuntime(
        FAST, role_engine("collector_fetcher"), role_sessions(WorkerRole.FETCH), tick=tick
    )
    with pytest.raises(RoleLoginError, match="collector_scheduler"):
        await asyncio.wait_for(fetcher.run(install_signals=False), timeout=15)
    assert fetcher.acquire_attempts == 0
    assert ticks == []


async def test_scheduler_runs_as_collector_scheduler(
    scheduler_engine: AsyncEngine,
    scheduler_sessions: async_sessionmaker[AsyncSession],
    wait_for: WaitFor,
    running: list[asyncio.Task[None]],
) -> None:
    """Дефолтний maintenance-тік (recover leases, stale instances) — у межах прав scheduler-а."""
    runtime = SchedulerRuntime(FAST, scheduler_engine, scheduler_sessions)
    task = asyncio.create_task(runtime.run(install_signals=False))
    running.append(task)
    await wait_for(lambda: runtime.ticks >= 2, what="scheduler планує під своєю роллю")
    runtime.request_stop()
    await asyncio.wait_for(task, timeout=15)


def _cli_env(url: URL) -> dict[str, str]:
    return {
        "COLLECTOR_POSTGRES_DSN": url.render_as_string(hide_password=False),
        "COLLECTOR_WORKER_PLACEHOLDER": "0",
        "COLLECTOR_WORKER_STOP_GRACE_SECONDS": "1",
    }


def _assert_clean_refusal(output: str, url: URL) -> None:
    assert "role login:" in output, output
    assert "Traceback" not in output, output
    if url.password:
        assert url.password not in output, "пароль DSN не потрапляє в stderr"


@pytest.mark.parametrize("argv", [["worker", "fetch"], ["scheduler"]])
def test_cli_exits_1_under_the_migration_superuser(
    pg_database: PostgresSettings, argv: list[str]
) -> None:
    result = runner.invoke(app, argv, env=_cli_env(pg_database.url))
    assert result.exit_code == 1, result.output
    _assert_clean_refusal(result.output, pg_database.url)
    assert "superuser" in result.output


def test_cli_worker_exits_1_with_the_dsn_of_another_component(
    login_urls: dict[str, URL],
) -> None:
    url = login_urls["collector_parser"]
    result = runner.invoke(app, ["worker", "fetch"], env=_cli_env(url))
    assert result.exit_code == 1, result.output
    _assert_clean_refusal(result.output, url)
    assert "collector_fetcher" in result.output
