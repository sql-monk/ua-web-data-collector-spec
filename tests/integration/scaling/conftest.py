"""Фікстури scaling-тестів WP-01D: PostgreSQL із WP-01A + керовані handler-и і очікування стану.

**Фікстури PostgreSQL не дублюються.** `tests/integration/postgres/conftest.py` (WP-01A) дає
template-БД, testcontainers і session/engine-фабрики; тут той самий модуль завантажується за
шляхом і його фікстури реекспортуються в цей каталог. Прямий `import` неможливий: у `tests/`
немає `__init__.py`, а `pytest_plugins` у не-кореневому conftest заборонений з pytest 7.
Fixturedef-и при цьому різні (свій у кожному conftest), тому спільним робиться сам **ресурс**:
`_share_server_and_template` підміняє `_start_container` і `TemplateState` memoized-обгортками,
і на процес припадає рівно один контейнер із однією template-БД (знахідка F6 gate 2).

**Runtime — під LOGIN-ролями §13, а не під superuser** (картка WP-01D PR1b): `WorkerRuntime`/
`SchedulerRuntime` при старті відмовляються працювати під superuser-ом, членом
`collector_migrate` чи роллю чужого компонента. Тому runtime у тестах отримує sessions своєї ролі
(`runtime_sessions`, `role_sessions(...)`, `scheduler_engine`/`scheduler_sessions`), а
`pg_sessions` (superuser) лишається лише для підготовки даних і перевірок стану — так само, як
оператор/міграції в production. Зразок — фікстури WP-01A `tests/integration/postgres/
test_role_logins.py`; паролі одноразові й генеруються в рантаймі. Ролі кластерні, тож після тесту
вони повертаються у NOLOGIN без пароля.

Детермінізм: тести чекають **стану**, а не часу — `wait_for` опитує предикат із коротким кроком
і падає з описом, якщо стан не настав. Жодного `sleep` на секунди й жодної залежності від
швидкості машини: lease TTL у тестах короткий, а «прострочення» моделюється явним `now` у
репозиторії (`recover_expired_leases(now=...)`), а не реальним очікуванням.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import secrets
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.engine import create_engine, create_session_factory
from collector.persistence.postgres.ops import apply_database_roles
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.persistence.postgres.roles import (
    RUNTIME_ROLES,
    dsn_secret_name,
    load_role_logins,
)
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import Task, TaskHandler, TaskResult
from collector.workers.roles import SCHEDULER_DB_ROLE, WorkerRole, db_role_for

pytestmark = pytest.mark.integration

_FIXTURES_MODULE = "collector_tests_postgres_fixtures"
_FIXTURES_PATH = Path(__file__).resolve().parent.parent / "postgres" / "conftest.py"
_SHARED: dict[str, Any] = {}


def _postgres_fixtures_module() -> Any:
    """Модуль фікстур WP-01A — по змозі **той самий обʼєкт**, який уже імпортував pytest.

    Спершу шукаємо серед завантажених модулів копію з тим самим `__file__` (pytest імпортує
    conftest каталогу `tests/integration/postgres` раніше за цей), і лише якщо її немає —
    завантажуємо файл за шляхом. Спільний обʼєкт модуля важливий для `_share_server_and_template`:
    патч його глобалів діє на обидва набори фікстур.
    """
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and Path(path).resolve() == _FIXTURES_PATH:
            return module
    spec = importlib.util.spec_from_file_location(_FIXTURES_MODULE, _FIXTURES_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover — шлях фіксований у репозиторії
        msg = f"не вдалося завантажити фікстури PostgreSQL з {_FIXTURES_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_FIXTURES_MODULE] = module
    spec.loader.exec_module(module)
    return module


def _share_server_and_template(module: Any) -> None:
    """Один контейнер PostgreSQL і одна template-БД на процес, а не по одному на каталог.

    Реекспорт фікстур дає два різні `fixturedef` (свій у кожному conftest), і кожен зі
    `scope="session"` кешується окремо — інакше локальний прогін піднімав би два контейнери
    (знахідка F6 gate 2). Тому спільним робиться не fixturedef, а сам ресурс: `_start_container`
    і `TemplateState` у модулі фікстур підміняються на memoized-обгортки. Контейнер зупиняється
    на виході з процесу (`atexit`), коли обидві сесійні фікстури вже завершені.
    """
    if _SHARED.get("patched"):
        return
    _SHARED["patched"] = True
    original_start = module._start_container  # noqa: SLF001 — навмисний патч тестової фікстури
    original_template = module.TemplateState

    def shared_start_container() -> Iterator[Any]:
        state = _SHARED.get("container")
        if state is None:
            generator = original_start()
            state = {"generator": generator, "server": next(generator)}
            _SHARED["container"] = state
            atexit.register(_stop_shared_container)
        yield state["server"]

    def shared_template_state() -> Any:
        state = _SHARED.get("template")
        if state is None:
            state = original_template()
            _SHARED["template"] = state
        return state

    module._start_container = shared_start_container  # noqa: SLF001 — див. докстрінг
    module.TemplateState = shared_template_state


def _stop_shared_container() -> None:
    state = _SHARED.pop("container", None)
    if state is None:
        return
    with suppress(Exception):  # контейнер уже міг зупинити Ryuk
        state["generator"].close()


_postgres_fixtures = _postgres_fixtures_module()
_share_server_and_template(_postgres_fixtures)

postgres_server = _postgres_fixtures.postgres_server
_template_state = _postgres_fixtures._template_state  # noqa: SLF001 — реекспорт фікстури WP-01A
pg_database = _postgres_fixtures.pg_database
pg_engine = _postgres_fixtures.pg_engine
pg_sessions = _postgres_fixtures.pg_sessions
pg_session = _postgres_fixtures.pg_session


# --- LOGIN-ролі §13 для runtime ------------------------------------------------------------


@pytest.fixture
async def login_urls(
    pg_database: PostgresSettings, tmp_path: Path
) -> AsyncIterator[dict[str, URL]]:
    """Увімкнути LOGIN усім runtime-ролям тим самим шляхом, що й `db roles --with-login`.

    DSN-секрети з одноразовими паролями пишуться в `tmp_path` і читаються `load_role_logins`;
    після тесту ролі повертаються у NOLOGIN без пароля (ролі кластерні, спільні для всіх БД).
    """
    urls: dict[str, URL] = {}
    for role in RUNTIME_ROLES:
        url = pg_database.url.set(username=role, password=secrets.token_hex(24))
        (tmp_path / dsn_secret_name(role)).write_text(
            url.render_as_string(hide_password=False) + "\n", encoding="utf-8"
        )
        urls[role] = url
    try:
        await apply_database_roles(pg_database, logins=load_role_logins(tmp_path))
        yield urls
    finally:
        admin = create_engine(pg_database, pool_size=1, max_overflow=0)
        try:
            async with admin.begin() as conn:
                for role in RUNTIME_ROLES:
                    await conn.execute(text(f'ALTER ROLE "{role}" WITH NOLOGIN PASSWORD NULL'))
        finally:
            await admin.dispose()


RoleEngine = Callable[[str], AsyncEngine]


@pytest.fixture
async def role_engine(login_urls: dict[str, URL]) -> AsyncIterator[RoleEngine]:
    """Фабрика engine під LOGIN-роллю (той самий `create_engine`, що й у CLI runtime)."""
    engines: dict[str, AsyncEngine] = {}

    def make(role: str) -> AsyncEngine:
        if role not in engines:
            engines[role] = create_engine(
                PostgresSettings(url=login_urls[role]),
                pool_size=10,
                max_overflow=6,
                application_name=f"pytest-{role}",
            )
        return engines[role]

    try:
        yield make
    finally:
        for engine in engines.values():
            await engine.dispose()


RoleSessions = Callable[[WorkerRole], async_sessionmaker[AsyncSession]]


@pytest.fixture
def role_sessions(role_engine: RoleEngine) -> RoleSessions:
    """Sessions під LOGIN-роллю worker-а заданої ролі (мапінг `db_role_for`)."""

    def make(role: WorkerRole) -> async_sessionmaker[AsyncSession]:
        return create_session_factory(role_engine(db_role_for(role)))

    return make


@pytest.fixture
def runtime_sessions(role_sessions: RoleSessions) -> async_sessionmaker[AsyncSession]:
    """Sessions runtime ролі `fetch` (default `worker_config`) — `collector_fetcher`."""
    return role_sessions(WorkerRole.FETCH)


@pytest.fixture
def scheduler_engine(role_engine: RoleEngine) -> AsyncEngine:
    """Engine scheduler-а під `collector_scheduler` (advisory lease + тік)."""
    return role_engine(SCHEDULER_DB_ROLE)


@pytest.fixture
def scheduler_sessions(scheduler_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(scheduler_engine)


T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
# Jobs ставляться в чергу «в минулому», щоб `not_before` не залежав від реального годинника
# машини (runtime claim-ить за справжнім now, а не за T0).
PAST = datetime(2020, 1, 1, tzinfo=UTC)
POLL_SECONDS = 0.01
DEFAULT_TIMEOUT = 15.0


class WaitFor(Protocol):
    async def __call__(
        self, predicate: Callable[[], bool], *, what: str, timeout: float = DEFAULT_TIMEOUT
    ) -> None: ...


@pytest.fixture
def wait_for() -> WaitFor:
    """Чекати на стан (не на час): опитування предиката до таймауту з описовою помилкою."""

    async def _wait_for(
        predicate: Callable[[], bool], *, what: str, timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if predicate():
                return
            if loop.time() >= deadline:
                pytest.fail(f"стан не настав за {timeout} с: {what}")
            await asyncio.sleep(POLL_SECONDS)

    return _wait_for


class ControlledHandler(TaskHandler):
    """Handler із керованим завершенням: тест сам вирішує, коли task «доробить».

    `release` відкритий — `handle` повертається одразу; закритий — task висить у роботі, і це
    дає детерміновані сценарії drain, hot-change concurrency і втрати lease.
    """

    def __init__(self, *, job_types: Sequence[str] = ("fetch",), blocking: bool = False) -> None:
        self._job_types = tuple(job_types)
        self.release = asyncio.Event()
        if not blocking:
            self.release.set()
        self.started: list[UUID] = []
        self.finished: list[UUID] = []
        self.cancelled: list[UUID] = []
        self.ready_checks = 0
        self.raise_error: Exception | None = None

    @property
    def job_types(self) -> tuple[str, ...]:
        return self._job_types

    @property
    def in_flight(self) -> int:
        return len(self.started) - len(self.finished) - len(self.cancelled)

    async def check_ready(self) -> None:
        self.ready_checks += 1

    async def handle(self, task: Task) -> TaskResult:
        self.started.append(task.job_id)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(task.job_id)
            raise
        if self.raise_error is not None:
            self.finished.append(task.job_id)
            raise self.raise_error
        self.finished.append(task.job_id)
        return TaskResult.success()


@pytest.fixture
def handler() -> ControlledHandler:
    return ControlledHandler()


@pytest.fixture
def blocking_handler() -> ControlledHandler:
    return ControlledHandler(blocking=True)


@pytest.fixture
def worker_config() -> Callable[..., WorkerRuntimeConfig]:
    """Конфігурація з короткими інтервалами: тести чекають на стан, а не на таймери."""

    def make(
        role: WorkerRole = WorkerRole.FETCH,
        *,
        lease_seconds: int = 60,
        heartbeat_seconds: float = 0.05,
        poll_seconds: float = 0.02,
        stop_grace_seconds: float = 10.0,
        claim_batch: int = 8,
        fence_after_seconds: float | None = None,
        max_concurrency: int | None = None,
        liveness_path: Path | None = None,
    ) -> WorkerRuntimeConfig:
        return WorkerRuntimeConfig(
            role=role,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            poll_seconds=poll_seconds,
            stop_grace_seconds=stop_grace_seconds,
            claim_batch=claim_batch,
            fence_after_seconds=fence_after_seconds,
            max_concurrency=max_concurrency,
            liveness_path=liveness_path,
            deployment="pytest",
            hostname="pytest-host",
            container_id="pytest-container",
        )

    return make


@pytest.fixture
def make_pool(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> Callable[..., Awaitable[int]]:
    """Створити pool ролі з явним desired state; повертає revision."""

    async def create(
        role: WorkerRole = WorkerRole.FETCH,
        *,
        replicas: int = 1,
        concurrency: int = 1,
        max_replicas: int = 4,
    ) -> int:
        async with pg_sessions() as session, session.begin():
            pool = await pools_repo.upsert_pool(
                session,
                role,
                pools_repo.PoolDesiredState(
                    desired_replicas=replicas,
                    desired_concurrency=concurrency,
                    max_replicas=max_replicas,
                ),
                actor="pytest",
                reason="scaling test",
                expected_revision=None,
                now=T0,
            )
            return pool.revision

    return create


@pytest.fixture
def enqueue_jobs(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> Callable[..., Awaitable[list[UUID]]]:
    """Покласти N jobs одного типу в чергу; повертає їхні `job_id`."""

    async def enqueue(count: int, *, job_type: str = "fetch", prefix: str = "job") -> list[UUID]:
        ids: list[UUID] = []
        async with pg_sessions() as session, session.begin():
            for index in range(count):
                job = await queue_repo.enqueue(
                    session,
                    queue_repo.NewJob(
                        job_type=job_type,
                        idempotency_key=f"{prefix}:{index}",
                        args={"n": index},
                    ),
                    now=PAST,
                )
                ids.append(job.job_id)
        return ids

    return enqueue


@pytest.fixture
async def running(role_engine: RoleEngine) -> AsyncIterator[list[asyncio.Task[None]]]:
    """Реєстр фонових runtime-задач: наприкінці тесту всі гарантовано зупинені.

    Залежить від `role_engine` навмисно (code review PR1b, low #2): pytest знімає фікстуру раніше
    за її залежності, тож runtime скасовується **до** `dispose()` engine-ів і
    `ALTER ROLE … NOLOGIN` — впалий посередині тест не засмічує логи `heartbeat_failed`/`fenced`
    через уже вимкнений логін.
    """
    del role_engine  # потрібна лише як залежність порядку teardown
    tasks: list[asyncio.Task[None]] = []
    yield tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
