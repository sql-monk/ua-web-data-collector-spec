"""Ролі worker pools §7.6: перелік ролей і їхні default desired-значення.

`WorkerRole` збігається з `worker_pools.role` і аргументом CLI `collector worker <role>`.
`DEFAULT_POOL_SPECS` — рядки таблиці §7.6 «Default replicas × concurrency»: вони
використовуються **лише** коли рядка `worker_pools` для ролі ще немає (перший boot на чистій
БД). Далі джерелом істини є desired state у PostgreSQL, який змінюють оператор/контролер, а
runtime лише читає його на кожному heartbeat.

`min_replicas`/`max_replicas` таблиця §7.6 не задає; тут вони — MVP-межі, у яких оператору
дозволено масштабувати pool без зміни коду (§7.7: `operator` скейлить у чинних min/max,
`admin` змінює самі межі). Уточнення меж — PR3 разом із `PoolController`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class WorkerRole(StrEnum):
    """Назви ролей збігаються з `worker_pools.role` і CLI `collector worker <role>`."""

    DISCOVERY = "discovery"
    FETCH = "fetch"
    BROWSER = "browser"
    PARSE = "parse"
    PROJECTOR = "projector"
    TRANSLATION = "translation"
    EXPORT = "export"
    MAINTENANCE = "maintenance"


@dataclass(frozen=True, slots=True)
class PoolSpec:
    """Default desired state ролі (§7.6) для першого створення `worker_pools`."""

    desired_replicas: int
    desired_concurrency: int
    min_replicas: int
    max_replicas: int
    resource_profile: str


def parse_worker_concurrency() -> int:
    """`parse`: «2 × CPU count» (§7.6) — кількість доступних процесу CPU, мінімум 2.

    `os.process_cpu_count()` враховує affinity/cgroup-квоту контейнера, тому в Compose з
    `cpus: "2.00"` не дає concurrency за кількістю ядер хоста.
    """
    return max(os.process_cpu_count() or 2, 2)


DEFAULT_POOL_SPECS: Final = MappingProxyType(
    {
        WorkerRole.DISCOVERY: PoolSpec(1, 4, 0, 4, "small"),
        WorkerRole.FETCH: PoolSpec(2, 8, 0, 8, "medium"),
        # browser: 0 × 1 — pool існує, але реплік за замовчуванням немає (§7.6, окремий image).
        WorkerRole.BROWSER: PoolSpec(0, 1, 0, 4, "large"),
        WorkerRole.PARSE: PoolSpec(2, parse_worker_concurrency(), 0, 8, "large"),
        WorkerRole.PROJECTOR: PoolSpec(1, 8, 0, 4, "medium"),
        WorkerRole.TRANSLATION: PoolSpec(1, 4, 0, 4, "small"),
        WorkerRole.EXPORT: PoolSpec(1, 2, 0, 2, "medium"),
        # maintenance: mutually exclusive named leases (§7.6) — більше однієї репліки не має сенсу.
        WorkerRole.MAINTENANCE: PoolSpec(1, 1, 0, 1, "small"),
    }
)
"""Default desired state кожної ролі; ключі покривають усі значення `WorkerRole`."""


def default_pool_spec(role: WorkerRole) -> PoolSpec:
    """Default desired state ролі (§7.6)."""
    return DEFAULT_POOL_SPECS[role]


SCHEDULER_DB_ROLE: Final = "collector_scheduler"
"""LOGIN-роль БД процесу `collector scheduler` (§13)."""

DB_ROLE_BY_WORKER_ROLE: Final = MappingProxyType(
    {
        WorkerRole.DISCOVERY: "collector_fetcher",
        WorkerRole.FETCH: "collector_fetcher",
        WorkerRole.BROWSER: "collector_fetcher",
        WorkerRole.PARSE: "collector_parser",
        WorkerRole.PROJECTOR: "collector_projector",
        WorkerRole.TRANSLATION: "collector_translation",
        # Тимчасово (рішення оркестратора, варіант (а) docs/plan/deps/WP-01A-to-WP-01D.md §1):
        # runtime-черга export-а пише worker_instances/crawl_jobs/audit_log, а read-only
        # `collector_export_ro` цього не може. Доменне читання даних експортом додасть власник
        # експорту окремим `collector_export_ro`-з'єднанням (ризик у картці WP-01D).
        WorkerRole.EXPORT: SCHEDULER_DB_ROLE,
        WorkerRole.MAINTENANCE: SCHEDULER_DB_ROLE,
    }
)
"""LOGIN-роль БД кожної ролі worker-а (§13, мапінг картки WP-01D PR1b п.1–2).

Процес перевіряє при старті, що підключився саме цією роллю (`collector.workers.login`):
DSN чужого компонента або спільний міграційний DSN — помилка конфігурації, а не робочий стан.
"""


def db_role_for(role: WorkerRole) -> str:
    """LOGIN-роль БД, під якою має працювати worker ролі `role` (§13)."""
    return DB_ROLE_BY_WORKER_ROLE[role]


__all__ = [
    "DB_ROLE_BY_WORKER_ROLE",
    "DEFAULT_POOL_SPECS",
    "SCHEDULER_DB_ROLE",
    "PoolSpec",
    "WorkerRole",
    "db_role_for",
    "default_pool_spec",
    "parse_worker_concurrency",
]
