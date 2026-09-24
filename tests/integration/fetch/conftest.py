"""Integration-фікстури fetch core (WP-02): PostgreSQL 18 з WP-01A + fakes мережі.

**Фікстури PostgreSQL не дублюються** — той самий прийом, що в `tests/integration/scaling/
conftest.py` (WP-01D): модуль `tests/integration/postgres/conftest.py` завантажується за шляхом,
його фікстури реекспортуються, а контейнер і template-БД спільні на процес.

HTTP — лише `respx`, DNS — `FakeResolver`; реальний сокет відкривається тільки до loopback
PostgreSQL (маркер `integration` → `allow_hosts` у `tests/conftest.py`).

**Проти мовчазного skip:** під `COLLECTOR_TEST_REQUIRE_DOCKER=1` будь-який skip у цьому
каталозі перетворюється на fail (`pytest_runtest_makereport` нижче) — Docker недоступний,
забутий `skipif` чи `pytest.skip` у фікстурі не дадуть «зеленого» прогону без виконаних тестів.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import os
import secrets
import sys
import time
from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from collector.storage import S3ArtifactStore, S3Credentials, StorageSettings

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "fetch"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))

REQUIRE_DOCKER_ENV = "COLLECTOR_TEST_REQUIRE_DOCKER"
_FIXTURES_MODULE = "collector_tests_postgres_fixtures"
_FIXTURES_PATH = Path(__file__).resolve().parent.parent / "postgres" / "conftest.py"
_SHARED: dict[str, Any] = {}
MINIO_IMAGE = "collector-minio:RELEASE.2025-09-07T16-13-09Z"
MINIO_URL_ENV = "COLLECTOR_TEST_MINIO_URL"
MINIO_ACCESS_KEY_ENV = "COLLECTOR_TEST_MINIO_ACCESS_KEY"
MINIO_SECRET_KEY_ENV = "COLLECTOR_TEST_MINIO_SECRET_KEY"  # noqa: S105 - env variable name


@dataclass(frozen=True, slots=True)
class MinioServer:
    settings: StorageSettings


def _skip_or_fail(reason: str) -> None:
    if os.environ.get(REQUIRE_DOCKER_ENV) == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _start_minio() -> Iterator[MinioServer]:
    try:
        from testcontainers.core.config import testcontainers_config
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.docker_client import DockerClient
    except ImportError as exc:  # pragma: no cover - dev dependency
        _skip_or_fail(f"testcontainers unavailable: {exc}")
        raise
    try:
        DockerClient().client.ping()
    except Exception as exc:
        _skip_or_fail(f"Docker unavailable ({type(exc).__name__}: {exc})")
        raise
    if not testcontainers_config.tc_host_override:
        testcontainers_config.tc_host_override = "127.0.0.1"
    access_key = f"test-{secrets.token_hex(8)}"
    secret_key = secrets.token_urlsafe(24)
    container = (
        DockerContainer(MINIO_IMAGE)
        .with_env("MINIO_ROOT_USER", access_key)
        .with_env("MINIO_ROOT_PASSWORD", secret_key)
        .with_command("server /data --console-address :9001")
        .with_exposed_ports(9000)
    )
    try:
        container.start()
    except Exception as exc:
        _skip_or_fail(f"MinIO container failed to start ({type(exc).__name__}: {exc})")
        raise
    try:
        endpoint = f"http://127.0.0.1:{container.get_exposed_port(9000)}"
        yield MinioServer(StorageSettings(endpoint, S3Credentials(access_key, secret_key)))
    finally:
        container.stop()


@pytest.fixture(scope="session")
def minio_server() -> Iterator[MinioServer]:
    endpoint = os.environ.get(MINIO_URL_ENV)
    access_key = os.environ.get(MINIO_ACCESS_KEY_ENV)
    secret_key = os.environ.get(MINIO_SECRET_KEY_ENV)
    if endpoint or access_key or secret_key:
        if not (endpoint and access_key and secret_key):
            pytest.fail(
                f"{MINIO_URL_ENV}, {MINIO_ACCESS_KEY_ENV}, and {MINIO_SECRET_KEY_ENV} "
                "must be set together"
            )
        yield MinioServer(StorageSettings(endpoint, S3Credentials(access_key, secret_key)))
        return
    yield from _start_minio()


@pytest_asyncio.fixture
async def minio_store(minio_server: MinioServer) -> AsyncIterator[tuple[S3ArtifactStore, str]]:
    store = S3ArtifactStore(minio_server.settings)
    bucket = f"wp02-{secrets.token_hex(8)}"
    deadline = time.monotonic() + 30
    while True:
        try:
            async with store._client() as client:  # noqa: SLF001 - test provisioning
                await client.create_bucket(Bucket=bucket)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.25)
    try:
        yield store, bucket
    finally:
        async with store._client() as client:  # noqa: SLF001 - test cleanup
            response = await client.list_objects_v2(Bucket=bucket)
            for item in response.get("Contents", []):
                await client.delete_object(Bucket=bucket, Key=item["Key"])
            await client.delete_bucket(Bucket=bucket)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[Any]:
    outcome = yield
    report = outcome.get_result()
    if report.skipped and os.environ.get(REQUIRE_DOCKER_ENV) == "1":
        report.outcome = "failed"
        report.longrepr = (
            f"{REQUIRE_DOCKER_ENV}=1: skip у tests/integration/fetch заборонено "
            f"(suite має реально виконатись): {report.longrepr}"
        )


def _postgres_fixtures_module() -> Any:
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
    """Один контейнер і одна template-БД на процес (див. докстрінг scaling/conftest.py)."""
    if getattr(module, "_collector_shared_resources", False):
        return
    module._collector_shared_resources = True  # noqa: SLF001 — маркер патчу тестової фікстури
    original_start = module._start_container  # noqa: SLF001
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

    module._start_container = shared_start_container  # noqa: SLF001
    module.TemplateState = shared_template_state


def _stop_shared_container() -> None:
    state = _SHARED.pop("container", None)
    if state is None:
        return
    with suppress(Exception):
        state["generator"].close()


_postgres_fixtures = _postgres_fixtures_module()
_share_server_and_template(_postgres_fixtures)

postgres_server = _postgres_fixtures.postgres_server
_template_state = _postgres_fixtures._template_state  # noqa: SLF001 — реекспорт фікстури WP-01A
pg_database = _postgres_fixtures.pg_database
pg_engine = _postgres_fixtures.pg_engine
pg_sessions = _postgres_fixtures.pg_sessions
pg_session = _postgres_fixtures.pg_session
