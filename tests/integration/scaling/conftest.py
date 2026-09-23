"""Фікстури scaling-тестів WP-01D: PostgreSQL із WP-01A + керовані handler-и і очікування стану.

**Фікстури PostgreSQL не дублюються.** `tests/integration/postgres/conftest.py` (WP-01A) дає
template-БД, testcontainers і session/engine-фабрики; тут той самий модуль завантажується за
шляхом і його фікстури реекспортуються в цей каталог. Прямий `import` неможливий: у `tests/`
немає `__init__.py`, а `pytest_plugins` у не-кореневому conftest заборонений з pytest 7. Ціна —
у сесії, де виконуються обидва каталоги і сервер піднімається testcontainers-ом, буде два
контейнери (два різні fixturedef-и session-scope); у CI сервер зовнішній
(`COLLECTOR_TEST_POSTGRES_ADMIN_DSN`), тож дублювання немає.

Детермінізм: тести чекають **стану**, а не часу — `wait_for` опитує предикат із коротким кроком
і падає з описом, якщо стан не настав. Жодного `sleep` на секунди й жодної залежності від
швидкості машини: lease TTL у тестах короткий, а «прострочення» моделюється явним `now` у
репозиторії (`recover_expired_leases(now=...)`), а не реальним очікуванням.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import Task, TaskHandler, TaskResult
from collector.workers.roles import WorkerRole

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
async def running() -> AsyncIterator[list[asyncio.Task[None]]]:
    """Реєстр фонових runtime-задач: наприкінці тесту всі гарантовано зупинені."""
    tasks: list[asyncio.Task[None]] = []
    yield tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
