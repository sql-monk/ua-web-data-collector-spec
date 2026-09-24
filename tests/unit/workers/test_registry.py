"""Реєстр доменних handler-ів і тіків з lazy import (WP-01D PR1c п.3, п.4, п.6) — без БД.

Доменні модулі моделюються тимчасовими пакетами в `tmp_path` (унікальне ім'я на тест, щоб
`sys.modules` не протікав між тестами). Перевіряється головне правило реєстру: `NoopHandler` —
лише коли модуля ролі ще немає; зламаний модуль або модуль без реєстрації → boot падає.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from structlog.testing import capture_logs
from typer.testing import CliRunner

from collector.cli import app
from collector.contracts import new_entity_id
from collector.persistence.postgres.clock import utcnow
from collector.workers import registry
from collector.workers.backends import CRAWL_JOBS, PROJECTION_TASKS
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import (
    HANDLER_FACTORIES,
    HandlerBinding,
    HandlerContext,
    NoopHandler,
    Task,
    TaskHandler,
    TaskResult,
)
from collector.workers.registry import (
    DomainTickSpec,
    HandlerRegistryError,
    as_bindings,
    load_domain_ticks,
    load_role_bindings,
)
from collector.workers.roles import WorkerRole
from collector.workers.runtime import WorkerRuntime

runner = CliRunner()


class StubHandler(TaskHandler):
    def __init__(self, *job_types: str) -> None:
        self._job_types = job_types or ("fetch.http",)

    @property
    def job_types(self) -> tuple[str, ...]:
        return self._job_types

    async def handle(self, task: Task) -> TaskResult:
        return TaskResult.success()


@pytest.fixture
def clean_registry() -> Iterator[None]:
    saved = dict(HANDLER_FACTORIES)
    try:
        HANDLER_FACTORIES.clear()
        yield
    finally:
        HANDLER_FACTORIES.clear()
        HANDLER_FACTORIES.update(saved)


ModuleFactory = Callable[[str], str]


@pytest.fixture
def domain_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_registry: None
) -> Iterator[ModuleFactory]:
    """Створити пакет `<unique>.handler` з заданим тілом; повертає dotted-ім'я модуля."""
    monkeypatch.syspath_prepend(str(tmp_path))
    created: list[str] = []

    def make(body: str) -> str:
        package = f"wp01d_domain_{uuid4().hex}"
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / package / "handler.py").write_text(body, encoding="utf-8")
        created.append(package)
        return f"{package}.handler"

    yield make
    for name in list(sys.modules):
        if any(name == package or name.startswith(package + ".") for package in created):
            del sys.modules[name]


def use_module(monkeypatch: pytest.MonkeyPatch, role: WorkerRole, module: str) -> None:
    monkeypatch.setattr(registry, "ROLE_HANDLER_MODULES", {role: module})


def context(role: WorkerRole = WorkerRole.FETCH, sessions: object | None = None) -> HandlerContext:
    return HandlerContext(
        role=role,
        sessions=cast("Any", sessions if sessions is not None else object()),
        worker_instance_id=new_entity_id(),
        clock=utcnow,
        env={},
    )


REGISTERING_MODULE = """
from collector.workers.handlers import HANDLER_FACTORIES, Task, TaskHandler, TaskResult
from collector.workers.roles import WorkerRole

SEEN = []


class DomainHandler(TaskHandler):
    def __init__(self, context):
        self.context = context

    @property
    def job_types(self):
        return ("fetch.http",)

    async def handle(self, task: Task) -> TaskResult:
        return TaskResult.success()


def factory(context):
    SEEN.append(context)
    return DomainHandler(context)


HANDLER_FACTORIES[WorkerRole.FETCH] = factory
"""


def test_static_map_matches_the_card() -> None:
    assert dict(registry.ROLE_HANDLER_MODULES) == {
        WorkerRole.FETCH: "collector.fetch.handler",
        WorkerRole.BROWSER: "collector.fetch.browser",
        WorkerRole.TRANSLATION: "collector.translation.handler",
        WorkerRole.PROJECTOR: "collector.workers.projector",
    }
    assert {spec.target for spec in registry.DOMAIN_TICKS} == {
        "collector.workers.reconciler:schedule",
        "collector.workers.compactor:schedule",
        "collector.workers.publisher:tick",
    }


def test_missing_role_module_falls_back_to_noop_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, clean_registry: None
) -> None:
    use_module(monkeypatch, WorkerRole.FETCH, f"wp01d_absent_{uuid4().hex}.handler")
    with capture_logs() as logs:
        bindings = load_role_bindings(context())
    assert [(b.backend, type(b.handler)) for b in bindings] == [(CRAWL_JOBS, NoopHandler)]
    events = [entry for entry in logs if entry["event"] == "worker.handler_module_missing"]
    assert len(events) == 1 and events[0]["log_level"] == "warning"


def test_missing_submodule_of_an_existing_package_also_falls_back_to_noop(
    monkeypatch: pytest.MonkeyPatch, domain_module: ModuleFactory
) -> None:
    """`collector.fetch` уже є (WP-02 PR1), а `collector.fetch.handler` ще ні — це не поломка."""
    package = domain_module("").rpartition(".")[0]
    use_module(monkeypatch, WorkerRole.FETCH, f"{package}.not_yet_merged")
    (binding,) = load_role_bindings(context())
    assert type(binding.handler) is NoopHandler


@pytest.mark.parametrize(
    "body",
    [
        "import wp01d_third_party_that_does_not_exist\n",
        "from collector.storage_not_merged_yet import upload\n",
        "raise ImportError('cannot import name X')\n",
        "raise RuntimeError('broken at import time')\n",
    ],
    ids=["missing-dependency", "missing-project-module", "import-error", "runtime-error"],
)
def test_broken_role_module_fails_boot_instead_of_a_silent_noop(
    monkeypatch: pytest.MonkeyPatch, domain_module: ModuleFactory, body: str
) -> None:
    use_module(monkeypatch, WorkerRole.FETCH, domain_module(body))
    with pytest.raises(HandlerRegistryError):
        load_role_bindings(context())


def test_module_that_does_not_register_its_factory_fails_boot(
    monkeypatch: pytest.MonkeyPatch, domain_module: ModuleFactory
) -> None:
    use_module(monkeypatch, WorkerRole.FETCH, domain_module("VALUE = 1\n"))
    with pytest.raises(HandlerRegistryError, match="не зареєстрував"):
        load_role_bindings(context())


def test_factory_receives_the_runtime_sessions_and_instance_id(
    monkeypatch: pytest.MonkeyPatch, domain_module: ModuleFactory
) -> None:
    module = domain_module(REGISTERING_MODULE)
    use_module(monkeypatch, WorkerRole.FETCH, module)
    sessions = cast("Any", object())
    runtime = WorkerRuntime(WorkerRuntimeConfig(role=WorkerRole.FETCH), sessions)

    (seen,) = sys.modules[module].SEEN
    assert seen.sessions is sessions, "той самий async_sessionmaker, не другий pool"
    assert seen.worker_instance_id == runtime.instance_id
    assert seen.owner == runtime.owner
    assert seen.role is WorkerRole.FETCH
    assert type(runtime.handler).__name__ == "DomainHandler"
    assert runtime.bindings[0].backend is CRAWL_JOBS


def test_factory_may_return_several_bindings(clean_registry: None) -> None:
    projector, reconcile = StubHandler("catalog_items"), StubHandler("projection.reconcile")
    HANDLER_FACTORIES[WorkerRole.PROJECTOR] = lambda _ctx: [
        HandlerBinding(PROJECTION_TASKS, projector),
        HandlerBinding(CRAWL_JOBS, reconcile),
    ]
    bindings = load_role_bindings(context(WorkerRole.PROJECTOR))
    assert [(b.backend, b.handler) for b in bindings] == [
        (PROJECTION_TASKS, projector),
        (CRAWL_JOBS, reconcile),
    ]


@pytest.mark.parametrize(
    "produced",
    [
        [],
        [object()],
        "fetch",
        None,
        [
            HandlerBinding(CRAWL_JOBS, StubHandler("projection.reconcile")),
            HandlerBinding(CRAWL_JOBS, StubHandler("projection.reconcile", "x")),
        ],
    ],
    ids=["empty", "not-a-binding", "string", "none", "overlapping-job-types"],
)
def test_invalid_factory_output_fails_boot(produced: object) -> None:
    with pytest.raises(HandlerRegistryError):
        as_bindings(produced, role=WorkerRole.PROJECTOR)


def test_same_job_type_on_different_queues_is_allowed() -> None:
    bindings = as_bindings(
        [
            HandlerBinding(PROJECTION_TASKS, StubHandler("x")),
            HandlerBinding(CRAWL_JOBS, StubHandler("x")),
        ],
        role=WorkerRole.PROJECTOR,
    )
    assert len(bindings) == 2


def test_factory_handler_breaking_the_contract_fails_boot(clean_registry: None) -> None:
    class SyncHandler(TaskHandler):
        @property
        def job_types(self) -> tuple[str, ...]:
            return ("fetch.http",)

        def handle(self, task: Task) -> TaskResult:  # type: ignore[override]  # навмисно sync
            return TaskResult.success()

    HANDLER_FACTORIES[WorkerRole.FETCH] = lambda _ctx: SyncHandler()
    with pytest.raises(TypeError, match="async def"):
        load_role_bindings(context())


def test_worker_cli_boot_exits_non_zero_on_a_broken_role_module(
    monkeypatch: pytest.MonkeyPatch, domain_module: ModuleFactory
) -> None:
    """Boot падає ще до підключення до БД: engine створено, але runtime не зібрався."""
    use_module(monkeypatch, WorkerRole.FETCH, domain_module("raise ImportError('boom')\n"))
    monkeypatch.setenv("COLLECTOR_POSTGRES_DSN", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN_FILE", raising=False)
    monkeypatch.delenv("COLLECTOR_WORKER_PLACEHOLDER", raising=False)
    result = runner.invoke(app, ["worker", "fetch"])
    assert result.exit_code != 0
    assert isinstance(result.exception, HandlerRegistryError)


def test_placeholder_bypasses_the_domain_import(
    monkeypatch: pytest.MonkeyPatch, domain_module: ModuleFactory
) -> None:
    module = domain_module("raise RuntimeError('must not be imported')\n")
    use_module(monkeypatch, WorkerRole.FETCH, module)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "collector.cli.placeholder_process", lambda name, owner: calls.append((name, owner))
    )
    monkeypatch.setenv("COLLECTOR_WORKER_PLACEHOLDER", "1")
    result = runner.invoke(app, ["worker", "fetch"])
    assert result.exit_code == 0, result.output
    assert calls == [("worker.fetch", "WP-01D")]
    assert module not in sys.modules


# --- доменні тіки ---------------------------------------------------------------------------


def write_tick_module(tmp_path: Path, body: str) -> str:
    name = f"wp01d_tick_{uuid4().hex}"
    (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")
    return name


@pytest.fixture
def tick_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def test_domain_tick_loads_async_function(tick_path: Path) -> None:
    module = write_tick_module(tick_path, "async def schedule(ctx):\n    return None\n")
    (loaded,) = load_domain_ticks({}, [DomainTickSpec("t", f"{module}:schedule", 1.0)])
    assert loaded.spec.name == "t"


def test_missing_domain_tick_module_is_skipped_with_a_warning() -> None:
    spec = DomainTickSpec("t", f"wp01d_absent_{uuid4().hex}:tick", 1.0)
    with capture_logs() as logs:
        assert load_domain_ticks({}, [spec]) == ()
    assert [entry["event"] for entry in logs] == ["scheduler.tick_module_missing"]


@pytest.mark.parametrize(
    ("body", "attr"),
    [
        ("raise ImportError('boom')\n", "tick"),
        ("VALUE = 1\n", "tick"),
        ("def tick(ctx):\n    return None\n", "tick"),
    ],
    ids=["import-error", "missing-attr", "sync-function"],
)
def test_broken_domain_tick_fails_scheduler_boot(tick_path: Path, body: str, attr: str) -> None:
    module = write_tick_module(tick_path, body)
    with pytest.raises(HandlerRegistryError):
        load_domain_ticks({}, [DomainTickSpec("t", f"{module}:{attr}", 1.0)])


def test_disabled_domain_tick_is_not_even_imported(tick_path: Path) -> None:
    module = write_tick_module(tick_path, "raise RuntimeError('must not be imported')\n")
    spec = DomainTickSpec("publisher", f"{module}:tick", 1.0, enabled_env="WP01D_TICK_ENABLED")
    assert load_domain_ticks({}, [spec]) == ()
    assert load_domain_ticks({"WP01D_TICK_ENABLED": "0"}, [spec]) == ()
    assert module not in sys.modules
    with pytest.raises(HandlerRegistryError):
        load_domain_ticks({"WP01D_TICK_ENABLED": "1"}, [spec])


def test_publisher_tick_is_disabled_by_default() -> None:
    (publisher,) = [spec for spec in registry.DOMAIN_TICKS if spec.name == "outbox.publish"]
    assert publisher.enabled_env == "COLLECTOR_OUTBOX_PUBLISHER_ENABLED"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": "no-colon", "interval_seconds": 1.0},
        {"target": "m:a", "interval_seconds": 0.0},
        {"target": "m:a", "interval_seconds": 1.0, "timeout_seconds": 0.0},
    ],
)
def test_domain_tick_spec_validates_itself(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="тік"):
        DomainTickSpec(name="t", **kwargs)
