"""Реєстр доменних handler-ів і доменних тіків scheduler-а з lazy import (PR1c п.3, п.6).

Доменний код живе в модулях інших WP; runtime його не імпортує на рівні модуля (інакше
`collector worker parse` тягнув би Playwright, Mongo-драйвер і провайдера перекладу). Замість
цього тут статична мапа «роль → модуль», і runtime імпортує модуль **своєї** ролі на boot. Модуль
при імпорті реєструє фабрику в `collector.workers.handlers.HANDLER_FACTORIES`.

Правила (жодного мовчазного `NoopHandler`, крім випадку «модуля ще немає»):

- `ModuleNotFoundError` **саме цього** модуля (або його пакета — модуля ще немає в `main`) →
  `NoopHandler` і warning `worker.handler_module_missing`: контейнер ролі живий, pool порожній;
- будь-який інший `ImportError` чи виняток під час імпорту (зламаний модуль, відсутня
  залежність) → `HandlerRegistryError`, boot падає ненульовим кодом;
- модуль імпортувався, але фабрику ролі не зареєстрував → `HandlerRegistryError`;
- фабрика повернула не `TaskHandler`/непорожню послідовність `HandlerBinding` або handler
  порушує контракт (`check_handler_contract`) → `HandlerRegistryError`/`TypeError`.

`COLLECTOR_WORKER_PLACEHOLDER=1` обходить усе це: CLI запускає placeholder-процес до створення
runtime, імпорту не відбувається.

Нові записи (WP-03 discovery, WP-05 parse, WP-11A export) — dependency-запитом до WP-01D. Для
`projector` модуль один (`collector.workers.projector`): його фабрика повертає **кілька**
прив'язок — `ProjectorHandler` на `projection_tasks` і handler-и `projection.reconcile`/
`projection.compact` (модулі `reconciler`/`compactor` WP-01B) на `crawl_jobs`. Одна фабрика на
роль — тому, що дві незалежні фабрики однієї ролі не мали б порядку і перетирали б одна одну.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
from typing import TYPE_CHECKING, Final

from collector.core.logging import get_logger
from collector.workers.backends import CRAWL_JOBS
from collector.workers.handlers import (
    HANDLER_FACTORIES,
    HandlerBinding,
    HandlerContext,
    NoopHandler,
    TaskHandler,
    check_handler_contract,
)
from collector.workers.roles import WorkerRole

if TYPE_CHECKING:
    from collector.workers.scheduler import TickContext

ROLE_HANDLER_MODULES: Final[Mapping[WorkerRole, str]] = MappingProxyType(
    {
        WorkerRole.FETCH: "collector.fetch.handler",
        WorkerRole.BROWSER: "collector.fetch.browser",
        WorkerRole.TRANSLATION: "collector.translation.handler",
        WorkerRole.PROJECTOR: "collector.workers.projector",
    }
)
"""Роль → модуль, який при імпорті реєструє `HANDLER_FACTORIES[role]` (PR1c п.3)."""

TRUTHY = frozenset({"1", "true", "yes"})


class HandlerRegistryError(RuntimeError):
    """Доменний модуль ролі/тіку зламаний або не зареєстрував себе — boot має впасти."""


@dataclass(frozen=True, slots=True)
class DomainTickSpec:
    """Доменний тік scheduler-а: `module:attr` з `async def tick(ctx: TickContext)`.

    `interval_seconds` — власний інтервал тіку; `timeout_seconds` — стеля одного проходу (тік,
    що завис, не зупиняє maintenance). `enabled_env` — тік вимкнений, доки ця змінна не
    `1/true/yes` (publisher N-2 без споживача не має позначати події опублікованими).
    """

    name: str
    target: str
    interval_seconds: float
    timeout_seconds: float = 60.0
    enabled_env: str | None = None

    def __post_init__(self) -> None:
        module, _, attr = self.target.partition(":")
        if not module or not attr:
            msg = f"тік {self.name!r}: target має форму 'module:attr', отримано {self.target!r}"
            raise ValueError(msg)
        if self.interval_seconds <= 0 or self.timeout_seconds <= 0:
            msg = f"тік {self.name!r}: interval_seconds і timeout_seconds мають бути > 0"
            raise ValueError(msg)

    @property
    def module(self) -> str:
        return self.target.partition(":")[0]

    @property
    def attr(self) -> str:
        return self.target.partition(":")[2]


DOMAIN_TICKS: Final[tuple[DomainTickSpec, ...]] = (
    # WP-01B: лише `queue.enqueue` задач `projection.reconcile`/`projection.compact` з
    # ідемпотентним ключем від вікна часу; виконує їх `projector-worker` (рішення оркестратора
    # WP-01B п.1 — під `collector_projector`).
    DomainTickSpec("projection.reconcile", "collector.workers.reconciler:schedule", 300.0),
    DomainTickSpec("projection.compact", "collector.workers.compactor:schedule", 3600.0),
    # WP-01B N-2: publisher outbox; вимкнений, доки немає споживача.
    DomainTickSpec(
        "outbox.publish",
        "collector.workers.publisher:tick",
        5.0,
        enabled_env="COLLECTOR_OUTBOX_PUBLISHER_ENABLED",
    ),
)
"""Доменні тіки scheduler-а (PR1c п.6). Контракт ідемпотентності — `docs/workers.md` §6."""

DomainTick = Callable[["TickContext"], Awaitable[None]]


def _is_missing(exc: ModuleNotFoundError, module: str) -> bool:
    """Чи означає виняток «модуля `module` ще немає», а не «модуль зламаний».

    `exc.name` — ім'я модуля, якого не знайшов імпорт. Якщо це сам `module` або його пакет
    (`collector.translation` для `collector.translation.handler`) — модуля ще немає в `main`.
    Будь-яке інше ім'я (сторонній пакет, інший модуль проєкту) — зламана залежність модуля.
    """
    name = exc.name
    if not name:
        return False
    return module == name or module.startswith(name + ".")


def import_optional(module: str) -> ModuleType | None:
    """Імпортувати `module`; `None` — модуля ще немає; зламаний модуль → `HandlerRegistryError`."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if _is_missing(exc, module):
            return None
        msg = f"модуль {module!r} не імпортується: бракує {exc.name!r} ({exc})"
        raise HandlerRegistryError(msg) from exc
    except Exception as exc:
        msg = f"модуль {module!r} впав під час імпорту: {type(exc).__name__}: {exc}"
        raise HandlerRegistryError(msg) from exc


def as_bindings(produced: object, *, role: WorkerRole) -> tuple[HandlerBinding, ...]:
    """Нормалізувати результат фабрики: `TaskHandler` → одна прив'язка до `crawl_jobs`.

    Дві прив'язки однієї черги з перетином `job_types` відхиляються: claim-или б ті самі tasks
    двома handler-ами, і хто саме виконає task, залежало б від порядку round-robin.
    """
    if isinstance(produced, TaskHandler):
        bindings: tuple[HandlerBinding, ...] = (HandlerBinding(CRAWL_JOBS, produced),)
    elif isinstance(produced, Sequence) and not isinstance(produced, str | bytes):
        bindings = tuple(produced)
        if not bindings or not all(isinstance(item, HandlerBinding) for item in bindings):
            msg = (
                f"фабрика ролі {role.value!r} має повернути TaskHandler або непорожню "
                "послідовність HandlerBinding"
            )
            raise HandlerRegistryError(msg)
    else:
        msg = f"фабрика ролі {role.value!r} повернула {type(produced).__name__}"
        raise HandlerRegistryError(msg)
    seen: dict[str, set[str]] = {}
    for binding in bindings:
        check_handler_contract(binding.handler)
        claimed = seen.setdefault(binding.backend.name, set())
        overlap = claimed & set(binding.handler.job_types)
        if overlap:
            msg = (
                f"роль {role.value!r}: job_types {sorted(overlap)} черги {binding.backend.name!r} "
                "обслуговують дві прив'язки"
            )
            raise HandlerRegistryError(msg)
        claimed.update(binding.handler.job_types)
    return bindings


def load_role_bindings(context: HandlerContext) -> tuple[HandlerBinding, ...]:
    """Прив'язки ролі: lazy import модуля → фабрика з реєстру → `HandlerBinding`-и.

    Фабрику вже могли зареєструвати напряму (тести, вбудований запуск) — тоді вона
    використовується і без модуля в мапі.
    """
    role = context.role
    log = get_logger(f"collector.worker.{role.value}")
    module = ROLE_HANDLER_MODULES.get(role)
    imported = False
    if module is not None:
        imported = import_optional(module) is not None
        if not imported:
            log.warning("worker.handler_module_missing", module=module, handler="NoopHandler")
    factory = HANDLER_FACTORIES.get(role)
    if factory is None:
        if imported:
            msg = (
                f"модуль {module!r} імпортовано, але він не зареєстрував "
                f"HANDLER_FACTORIES[WorkerRole.{role.name}]"
            )
            raise HandlerRegistryError(msg)
        return (HandlerBinding(CRAWL_JOBS, NoopHandler(role)),)
    return as_bindings(factory(context), role=role)


@dataclass(frozen=True, slots=True)
class LoadedTick:
    """Доменний тік, готовий до запуску scheduler-ом."""

    spec: DomainTickSpec
    run: DomainTick


def load_domain_ticks(
    environ: Mapping[str, str], specs: Sequence[DomainTickSpec] = DOMAIN_TICKS
) -> tuple[LoadedTick, ...]:
    """Доменні тіки з lazy import: відсутній модуль → warning і пропуск; зламаний → помилка."""
    log = get_logger("collector.scheduler")
    loaded: list[LoadedTick] = []
    for spec in specs:
        if spec.enabled_env is not None:
            if environ.get(spec.enabled_env, "").strip().lower() not in TRUTHY:
                log.info("scheduler.tick_disabled", tick=spec.name, enabled_env=spec.enabled_env)
                continue
        module = import_optional(spec.module)
        if module is None:
            log.warning("scheduler.tick_module_missing", tick=spec.name, module=spec.module)
            continue
        run = getattr(module, spec.attr, None)
        if run is None or not inspect.iscoroutinefunction(run):
            msg = (
                f"тік {spec.name!r}: {spec.target!r} має бути `async def {spec.attr}(ctx)`, "
                f"отримано {type(run).__name__}"
            )
            raise HandlerRegistryError(msg)
        loaded.append(LoadedTick(spec=spec, run=run))
    return tuple(loaded)


__all__ = [
    "DOMAIN_TICKS",
    "ROLE_HANDLER_MODULES",
    "DomainTick",
    "DomainTickSpec",
    "HandlerRegistryError",
    "LoadedTick",
    "as_bindings",
    "import_optional",
    "load_domain_ticks",
    "load_role_bindings",
]
