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

import atexit
import importlib.util
import os
import sys
from collections.abc import Generator, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "fetch"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))

REQUIRE_DOCKER_ENV = "COLLECTOR_TEST_REQUIRE_DOCKER"
_FIXTURES_MODULE = "collector_tests_postgres_fixtures"
_FIXTURES_PATH = Path(__file__).resolve().parent.parent / "postgres" / "conftest.py"
_SHARED: dict[str, Any] = {}


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
