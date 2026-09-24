"""Integration-фікстури MongoDB 8.0 replica set (WP-01B): власна БД на кожен тест.

- сервер: `COLLECTOR_TEST_MONGO_URI` (+ `COLLECTOR_TEST_MONGO_ROOT_USERNAME`,
  `COLLECTOR_TEST_MONGO_ROOT_PASSWORD[_FILE]`) —
  зовнішній mongod (CI job `integration-mongo`: `docker run` у кроці), інакше `testcontainers`
  з тим самим pinned digest, що в compose. Docker недоступний → skip локально, але
  `pytest.fail` при `COLLECTOR_TEST_REQUIRE_DOCKER=1` (як `_skip_or_fail` у postgres/conftest);
- mongod: `--replSet rs0 --keyFile` (auth + RS вимагає keyFile) і **лише в тестах**
  `--setParameter enableTestCommands=1` (`configureFailPoint`, PR2). Root-пароль і keyfile
  генеруються в рантаймі (без секретів у репозиторії);
- мережа: лише loopback. RS ініціалізується з member host `127.0.0.1:27017` (адреса всередині
  контейнера), а всі клієнти тестів ходять на mapped port з `directConnection=true` — драйвер не
  йде за member host-ом RS (інакше на Linux `pytest-socket` або DNS `mongo` зламали б CI);
- ізоляція: кожен тест — БД `collector_test_<uuid>`, дропається після тесту; custom roles і
  користувачі (глобальні в `admin`) прибираються фікстурою `mongo_users_cleanup`.

Вартовий проти мовчазного skip (картка WP-01B, «Спільні вимоги»): при
`COLLECTOR_TEST_REQUIRE_DOCKER=1` будь-який skipped/xfail тест цього каталогу або менше за
`MIN_COLLECTED_TESTS` вибраних тестів → ненульовий exit (`pytest_sessionfinish`).
"""

from __future__ import annotations

import os
import secrets
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pymongo import AsyncMongoClient, MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.database import Database
from pymongo.errors import PyMongoError

from collector.cli import ensure_mongo_replica_set
from collector.core.config import env_or_file
from collector.persistence.mongo.client import MongoSettings, create_client
from collector.persistence.mongo.users import AUTH_DATABASE, USER_COMPONENTS, user_name

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "mongo"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))

pytestmark = pytest.mark.integration

MONGO_IMAGE = "mongo:8.0@sha256:4968f22d0c6c10ef29952f3e807f62872ba22b3312f25803564fbfc08255efc2"
MONGO_URI_ENV = "COLLECTOR_TEST_MONGO_URI"
MONGO_USER_ENV = "COLLECTOR_TEST_MONGO_ROOT_USERNAME"
MONGO_PASSWORD_ENV = "COLLECTOR_TEST_MONGO_ROOT_PASSWORD"  # noqa: S105 — ім'я env, не пароль
REQUIRE_DOCKER_ENV = "COLLECTOR_TEST_REQUIRE_DOCKER"
REPLICA_SET = "rs0"
MEMBER_HOST = "127.0.0.1:27017"
READY_TIMEOUT_SECONDS = 90.0
MIN_COLLECTED_TESTS = 20
"""Мінімум вибраних тестів `tests/integration/mongo` для PR1 (вартовий проти тихого зникнення)."""

GUARD_ROOT = Path(__file__).resolve().parent
KEYFILE_SCRIPT = (
    'printf %s "$COLLECTOR_TEST_KEYFILE" > /tmp/keyfile && chmod 400 /tmp/keyfile '
    "&& chown mongodb:mongodb /tmp/keyfile "
    '&& exec docker-entrypoint.sh mongod --replSet "$COLLECTOR_TEST_REPLSET" '
    "--keyFile /tmp/keyfile --bind_ip_all --setParameter enableTestCommands=1"
)


@dataclass(frozen=True, slots=True)
class MongoServer:
    """Loopback-адреса mongod і root-облікові дані (одноразові, лише для тестового контейнера)."""

    host: str
    port: int
    username: str
    password: str

    def uri(self, username: str | None = None, password: str | None = None) -> str:
        from urllib.parse import quote_plus

        user = quote_plus(username or self.username)
        secret = quote_plus(password or self.password)
        return (
            f"mongodb://{user}:{secret}@{self.host}:{self.port}/"
            "?authSource=admin&directConnection=true"
        )

    def sync_client(self, **kwargs: Any) -> MongoClient[dict[str, Any]]:
        options: dict[str, Any] = {
            "directConnection": True,
            "serverSelectionTimeoutMS": 10_000,
            "uuidRepresentation": "standard",
            "tz_aware": True,
            "w": "majority",
        }
        options.update(kwargs)
        return MongoClient(self.uri(), **options)


def _skip_or_fail(reason: str) -> None:
    if os.environ.get(REQUIRE_DOCKER_ENV) == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _wait_ready(server: MongoServer) -> None:
    """Чекає на фінальний mongod (entrypoint спершу стартує тимчасовий без RS) і ініціює RS."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        client = server.sync_client(serverSelectionTimeoutMS=2_000)
        try:
            client.admin.command("ping")
            ensure_mongo_replica_set(client, replica_set=REPLICA_SET, member_host=MEMBER_HOST)
            return
        except PyMongoError as exc:
            last = exc
            time.sleep(1.0)
        finally:
            client.close()
    _skip_or_fail(f"MongoDB не став primary за {READY_TIMEOUT_SECONDS}s: {last!r}")


def _start_container() -> Iterator[MongoServer]:
    try:
        from testcontainers.core.config import testcontainers_config
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.docker_client import DockerClient
    except ImportError as exc:  # pragma: no cover - dev-залежність
        _skip_or_fail(f"testcontainers недоступний: {exc}")
        raise
    try:
        DockerClient().client.ping()
    except Exception as exc:  # будь-яка помилка daemon = Docker недоступний
        _skip_or_fail(f"Docker недоступний ({type(exc).__name__}: {exc}) — integration skip")
        raise
    if not testcontainers_config.tc_host_override:
        testcontainers_config.tc_host_override = "127.0.0.1"
    username = "collector_test_root"
    password = secrets.token_hex(16)  # одноразовий, лише loopback-контейнер
    container = (
        DockerContainer(MONGO_IMAGE)
        .with_env("MONGO_INITDB_ROOT_USERNAME", username)
        .with_env("MONGO_INITDB_ROOT_PASSWORD", password)
        .with_env("COLLECTOR_TEST_KEYFILE", secrets.token_hex(64))  # keyfile: лише base64-алфавіт
        .with_env("COLLECTOR_TEST_REPLSET", REPLICA_SET)
        .with_exposed_ports(27017)
        # tmpfs для даних: DDL на single-member RS чекає journal/majority commit, а файловий I/O
        # Docker Desktop робить кожен createIndex секундами; стан тестам між запусками не потрібен.
        .with_tmpfs_mount("/data/db", "rw,mode=1777")
        .with_tmpfs_mount("/data/configdb", "rw,mode=1777")
        .with_kwargs(entrypoint=["bash", "-ec", KEYFILE_SCRIPT])
    )
    container.start()
    try:
        server = MongoServer(
            host="127.0.0.1",
            port=int(container.get_exposed_port(27017)),
            username=username,
            password=password,
        )
        yield server
    finally:
        container.stop()


@pytest.fixture(scope="session")
def mongo_server() -> Iterator[MongoServer]:
    """Адреса сервера без жодного з'єднання (готовність — лениво в `mongo_root`).

    Session-фікстури виконуються до того, як `pytest-socket` застосує `allow_hosts` першого
    тесту, тож з'єднання тут блокувалися б (на Linux — `--disable-socket`); Docker API ходить
    через npipe/unix socket.
    """
    external = os.environ.get(MONGO_URI_ENV)
    if external:
        from pymongo.uri_parser import parse_uri

        host, port = parse_uri(external)["nodelist"][0]
        server = MongoServer(
            host=host,
            port=int(port),
            username=os.environ.get(MONGO_USER_ENV, ""),
            password=env_or_file(MONGO_PASSWORD_ENV) or "",
        )
        if not server.username or not server.password:
            _skip_or_fail(f"{MONGO_URI_ENV} задано без {MONGO_USER_ENV}/{MONGO_PASSWORD_ENV}")
        yield server
        return
    yield from _start_container()


@pytest.fixture(scope="session")
def _mongo_ready() -> dict[str, bool]:
    return {"ready": False}


@pytest.fixture
def mongo_root(
    mongo_server: MongoServer, _mongo_ready: dict[str, bool]
) -> Iterator[MongoClient[dict[str, Any]]]:
    """Root-клієнт на один тест; перший виклик чекає primary та ініціює RS."""
    if not _mongo_ready["ready"]:
        _wait_ready(mongo_server)
        _mongo_ready["ready"] = True
    client = mongo_server.sync_client()
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def mongo_db_name(mongo_root: MongoClient[dict[str, Any]]) -> Iterator[str]:
    name = f"collector_test_{uuid.uuid4().hex[:12]}"
    try:
        yield name
    finally:
        mongo_root.drop_database(name)


@pytest.fixture
def mongo_db(
    mongo_root: MongoClient[dict[str, Any]], mongo_db_name: str
) -> Database[dict[str, Any]]:
    return mongo_root.get_database(mongo_db_name)


@pytest.fixture
async def mongo_async_db(
    mongo_server: MongoServer, mongo_db_name: str
) -> AsyncIterator[AsyncDatabase[dict[str, Any]]]:
    """Async-клієнт з concerns §8 (`create_client`) на ту саму тестову БД (root)."""
    settings = MongoSettings(uri=mongo_server.uri(), database=mongo_db_name)
    client: AsyncMongoClient[dict[str, Any]] = create_client(settings)
    try:
        yield client.get_database(mongo_db_name)
    finally:
        await client.close()


@pytest.fixture
def mongo_users_cleanup(mongo_root: MongoClient[dict[str, Any]]) -> Iterator[None]:
    """Прибирає глобальні (у `admin`) користувачів/ролі компонентів після тесту."""
    yield
    admin = mongo_root[AUTH_DATABASE]
    for component in USER_COMPONENTS:
        name = user_name(component)
        if admin.command("usersInfo", name)["users"]:
            admin.command("dropUser", name)
        if admin.command("rolesInfo", name)["roles"]:
            admin.command("dropRole", name)


# --- вартовий проти мовчазного skip ----------------------------------------------------------


def guard_failures(
    *, required: bool, collected: int, skipped: Sequence[str], minimum: int
) -> list[str]:
    """Причини провалу сесії (порожньо — ок). Чиста функція: її перевіряє unit-тест."""
    if not required:
        return []
    problems = [f"skipped/xfail у tests/integration/mongo: {nodeid}" for nodeid in skipped]
    if collected < minimum:
        problems.append(
            f"зібрано {collected} integration-тестів Mongo, мінімум — {minimum} "
            "(тест зник або не зібрався)"
        )
    return problems


_GUARD: dict[str, Any] = {"collected": 0, "skipped": []}


def _in_guard_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(GUARD_ROOT)
    except ValueError:
        return False
    return True


def pytest_collection_finish(session: pytest.Session) -> None:
    _GUARD["collected"] = sum(1 for item in session.items if _in_guard_root(item.path))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped and _in_guard_root(Path(str(report.fspath))):
        _GUARD["skipped"].append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    problems = guard_failures(
        required=os.environ.get(REQUIRE_DOCKER_ENV) == "1",
        collected=int(_GUARD["collected"]),
        skipped=list(_GUARD["skipped"]),
        minimum=MIN_COLLECTED_TESTS,
    )
    if problems:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        for problem in problems:
            if reporter is not None:
                reporter.write_line(f"MONGO GUARD: {problem}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
