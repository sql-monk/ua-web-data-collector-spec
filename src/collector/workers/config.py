"""Конфігурація worker- і scheduler-runtime з env (Compose/Swarm задають її через environment).

Жодного локального стану (§15, FR-031): конфігурація читається лише з env, `worker_instance_id`
генерується на boot (`collector.workers.runtime`), а desired concurrency береться з
`worker_pools` у PostgreSQL, а не з env — інакше hot-change з GUI (§7.6) мав би другий,
розсинхронізований источник істини.

Env-змінні worker-а:

| Змінна | Типово | Призначення |
|---|---|---|
| `COLLECTOR_WORKER_LEASE_SECONDS` | `60` | TTL lease job-и (`crawl_jobs.lease_expires_at`) |
| `COLLECTOR_WORKER_HEARTBEAT_SECONDS` | `20` | період heartbeat; має бути ≤ половини lease TTL |
| `COLLECTOR_WORKER_POLL_SECONDS` | `1.0` | пауза claim-loop, коли черга порожня або слоти зайняті |
| `COLLECTOR_WORKER_STOP_GRACE_SECONDS` | `90` | бюджет drain по SIGTERM (< Compose grace) |
| `COLLECTOR_WORKER_CLAIM_BATCH` | `8` | максимум jobs за один claim (не більше вільних слотів) |
| `COLLECTOR_WORKER_FENCE_AFTER_SECONDS` | ½ lease TTL | вікно до self-fencing |
| `COLLECTOR_WORKER_MAX_CONCURRENCY` | default ролі §7.6 | стеля слотів = розмір pool з'єднань |
| `COLLECTOR_WORKER_LIVENESS_FILE` | `$TMPDIR/…alive` | маркер liveness (healthcheck) |
| `COLLECTOR_WORKER_DEPLOYMENT` | `compose` | metadata `worker_instances.deployment` |
| `COLLECTOR_CONTAINER_ID` | `HOSTNAME` | metadata `worker_instances.container_id` |

Env-змінні scheduler-а: `COLLECTOR_SCHEDULER_TICK_SECONDS` (`5`),
`COLLECTOR_SCHEDULER_LEASE_RETRY_SECONDS` (`5`), `COLLECTOR_SCHEDULER_LEASE_NAME`
(`scheduler`), `COLLECTOR_SCHEDULER_STALE_AFTER_SECONDS` (`60`).
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from collector.core.version import version_info
from collector.workers.liveness import default_liveness_path
from collector.workers.roles import WorkerRole, default_pool_spec

WORKER_ENV_PREFIX = "COLLECTOR_WORKER_"
SCHEDULER_ENV_PREFIX = "COLLECTOR_SCHEDULER_"
CONTAINER_ID_ENV = "COLLECTOR_CONTAINER_ID"
PLACEHOLDER_ENV = "COLLECTOR_WORKER_PLACEHOLDER"
INSTANCE_VERSION_MAX_LENGTH = 128
FENCE_RATIO = 0.5
"""Частка lease TTL, після якої непідтверджений heartbeat означає втрачений lease."""
WATCHDOG_STEPS = 4
"""Скільки разів за вікно fencing прокидається сторож lease."""
HEARTBEAT_BUDGET_RATIO = 2.0
"""У скільки разів бюджет одного heartbeat-тіку більший за вікно fencing (сторож — першим)."""
MISSED_BEATS_BUDGET = 3
"""Скільки періодів heartbeat має вміщатись у lease TTL."""
MAX_SLOTS_UNBOUNDED = 1_000_000
"""`max_concurrency=None` — вбудований запуск без власного engine: стелі немає."""
CONNECTION_RESERVE = 3
"""Скільки з'єднань понад слоти потрібно процесу: heartbeat + claim + зміна статусу."""


class WorkerConfigError(ValueError):
    """Невалідна конфігурація runtime (env або аргументи)."""


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        msg = f"{name}={raw!r}: очікується число"
        raise WorkerConfigError(msg) from exc
    if value <= 0:
        msg = f"{name}={raw!r}: значення має бути > 0"
        raise WorkerConfigError(msg)
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{name}={raw!r}: очікується ціле число"
        raise WorkerConfigError(msg) from exc
    if value < 1:
        msg = f"{name}={raw!r}: значення має бути >= 1"
        raise WorkerConfigError(msg)
    return value


def instance_version() -> str:
    """Версія процесу для `worker_instances.version`: `<package>+<git sha>` (обрізана)."""
    info = version_info()
    return f"{info.package_version}+{info.git_sha}"[:INSTANCE_VERSION_MAX_LENGTH]


def placeholder_requested(environ: Mapping[str, str] | None = None) -> bool:
    """Rollback-прапорець: `COLLECTOR_WORKER_PLACEHOLDER=1` повертає placeholder-процес WP-00.

    Потрібен, щоб зупинити claim без перебудови image, якщо runtime поводиться погано в
    production (розділ «Rollback/disable» картки WP-01D).
    """
    return _env(environ).get(PLACEHOLDER_ENV, "").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    """Параметри одного worker-процесу; `desired_concurrency` тут немає навмисно (див. модуль)."""

    role: WorkerRole
    lease_seconds: int = 60
    heartbeat_seconds: float = 20.0
    poll_seconds: float = 1.0
    stop_grace_seconds: float = 90.0
    claim_batch: int = 8
    # Скільки жити без підтвердженого базою heartbeat, перш ніж скасувати активні tasks
    # (self-fencing, `runtime._fence_if_lease_unconfirmed`). `None` → половина lease TTL:
    # один пропущений heartbeat пробачається, два — вже ризик подвійного виконання.
    fence_after_seconds: float | None = None
    # Стеля concurrency цього процесу: розмір pool з'єднань фіксується на старті, тому гарячий
    # `desired_concurrency` понад цю межу обрізається із попередженням (M-3 код-рев'ю).
    # `None` — вбудований запуск (тести), де engine створює викликач.
    max_concurrency: int | None = None
    # Маркер liveness для Docker healthcheck (вимога 7 картки). `None` — вимкнено: вбудований
    # запуск і тести не мають писати нічого, навіть у tmpfs.
    liveness_path: Path | None = None
    deployment: str = "compose"
    hostname: str | None = field(default=None)
    container_id: str | None = None
    version: str = field(default_factory=instance_version)

    def __post_init__(self) -> None:
        if self.lease_seconds < 1:
            msg = f"lease_seconds має бути >= 1, отримано {self.lease_seconds}"
            raise WorkerConfigError(msg)
        if self.heartbeat_seconds * MISSED_BEATS_BUDGET > self.lease_seconds:
            # Пропущений heartbeat (рестарт з'єднання, пауза GC) не повинен коштувати lease:
            # інакше job відбирає `recover_expired_leases` у живого worker-а (§7.6). Запас у
            # три періоди, а не два: другий тік інакше припадав би рівно на момент експірації,
            # і будь-який RTT робив би пропуск фатальним (L-1 код-рев'ю).
            msg = (
                f"heartbeat_seconds ({self.heartbeat_seconds}) має бути <= третини "
                f"lease_seconds ({self.lease_seconds})"
            )
            raise WorkerConfigError(msg)
        if self.max_concurrency is not None and self.max_concurrency < 1:
            msg = f"max_concurrency має бути >= 1, отримано {self.max_concurrency}"
            raise WorkerConfigError(msg)
        if self.claim_batch < 1:
            msg = f"claim_batch має бути >= 1, отримано {self.claim_batch}"
            raise WorkerConfigError(msg)
        if self.fence_after_seconds is not None and not (
            0 < self.fence_after_seconds <= self.lease_seconds
        ):
            msg = (
                f"fence_after_seconds ({self.fence_after_seconds}) має бути у (0, "
                f"lease_seconds={self.lease_seconds}]: після нього runtime вважає lease "
                "втраченим і скасовує активні tasks"
            )
            raise WorkerConfigError(msg)

    @property
    def fence_after(self) -> float:
        """Безпечне вікно без підтвердженого heartbeat (типово половина lease TTL)."""
        if self.fence_after_seconds is not None:
            return self.fence_after_seconds
        return self.lease_seconds * FENCE_RATIO

    @property
    def watchdog_interval(self) -> float:
        """Крок сторожа lease: помітно частіше за вікно fencing, але не частіше за 10 мс."""
        return max(min(self.fence_after / WATCHDOG_STEPS, self.heartbeat_seconds), 0.01)

    @property
    def heartbeat_tick_budget(self) -> float:
        """Стеля тривалості одного heartbeat-тіку.

        Свідомо більша за `fence_after`: першим спрацьовує сторож (він і є гарантією), а цей
        бюджет лише не дає завислій корутині жити вічно і тримати з'єднання.
        """
        return self.fence_after * HEARTBEAT_BUDGET_RATIO

    @property
    def statement_timeout_ms(self) -> int:
        """`SET LOCAL statement_timeout` для транзакції heartbeat, мс (мінімум 100)."""
        return max(int(self.fence_after * 1000), 100)

    @property
    def command_timeout(self) -> float:
        """`command_timeout` asyncpg для engine, який створює `collector.cli` (S-4).

        Це **клієнтська** межа на кожен запит процесу. Друга, серверна, — `statement_timeout`
        у кожній транзакції runtime (`workers.session.bounded_transaction`,
        `statement_timeout_ms`). Разом вони покривають обидва типи відмови: сервер не
        відповідає взагалі / запит виконується надто довго. Вбудований запуск (тести, кілька
        runtime в одному процесі) створює engine сам і може не мати `command_timeout` — там
        нижню межу тримають `statement_timeout` і сторож lease.
        """
        return max(self.fence_after, 1.0)

    @property
    def max_slots(self) -> int:
        """Стеля слотів цього процесу: більше не дозволяє розмір pool з'єднань (M-3)."""
        return self.max_concurrency if self.max_concurrency is not None else MAX_SLOTS_UNBOUNDED

    @classmethod
    def from_env(
        cls, role: WorkerRole, environ: Mapping[str, str] | None = None
    ) -> WorkerRuntimeConfig:
        """Конфігурація з env (див. таблицю в docstring модуля)."""
        env = _env(environ)
        return cls(
            role=role,
            lease_seconds=_positive_int(env, f"{WORKER_ENV_PREFIX}LEASE_SECONDS", 60),
            heartbeat_seconds=_positive_float(env, f"{WORKER_ENV_PREFIX}HEARTBEAT_SECONDS", 20.0),
            poll_seconds=_positive_float(env, f"{WORKER_ENV_PREFIX}POLL_SECONDS", 1.0),
            stop_grace_seconds=_positive_float(env, f"{WORKER_ENV_PREFIX}STOP_GRACE_SECONDS", 90.0),
            claim_batch=_positive_int(env, f"{WORKER_ENV_PREFIX}CLAIM_BATCH", 8),
            fence_after_seconds=(
                _positive_float(env, f"{WORKER_ENV_PREFIX}FENCE_AFTER_SECONDS", 0.0) or None
            ),
            max_concurrency=_positive_int(
                env,
                f"{WORKER_ENV_PREFIX}MAX_CONCURRENCY",
                default_pool_spec(role).desired_concurrency,
            ),
            liveness_path=default_liveness_path(dict(env)),
            deployment=env.get(f"{WORKER_ENV_PREFIX}DEPLOYMENT", "compose").strip() or "compose",
            # Docker hostname — лише metadata (§7.5): за нею не приймається жодне рішення.
            hostname=socket.gethostname(),
            # `HOSTNAME` у контейнері Docker/Swarm — короткий id контейнера (те саме, що
            # показує `docker ps`), тому колонка `worker_instances.container_id` заповнена без
            # додаткової конфігурації; явний `COLLECTOR_CONTAINER_ID` має пріоритет.
            container_id=(
                env.get(CONTAINER_ID_ENV, "").strip() or env.get("HOSTNAME", "").strip() or None
            ),
        )


@dataclass(frozen=True, slots=True)
class SchedulerRuntimeConfig:
    """Параметри singleton scheduler-а (advisory lease + maintenance tick)."""

    lease_name: str = "scheduler"
    tick_seconds: float = 5.0
    lease_retry_seconds: float = 5.0
    stale_after_seconds: float = 60.0
    recover_limit: int = 1000
    liveness_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.lease_name:
            msg = "lease_name не може бути порожнім"
            raise WorkerConfigError(msg)
        if self.recover_limit < 1:
            msg = f"recover_limit має бути >= 1, отримано {self.recover_limit}"
            raise WorkerConfigError(msg)

    @property
    def statement_timeout_ms(self) -> int:
        """`statement_timeout` транзакції тіку, мс: тік не має пережити власний інтервал."""
        return max(int((self.tick_seconds + self.lease_retry_seconds) * 1000), 1000)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SchedulerRuntimeConfig:
        env = _env(environ)
        name = env.get(f"{SCHEDULER_ENV_PREFIX}LEASE_NAME", "scheduler").strip() or "scheduler"
        return cls(
            lease_name=name,
            tick_seconds=_positive_float(env, f"{SCHEDULER_ENV_PREFIX}TICK_SECONDS", 5.0),
            lease_retry_seconds=_positive_float(
                env, f"{SCHEDULER_ENV_PREFIX}LEASE_RETRY_SECONDS", 5.0
            ),
            stale_after_seconds=_positive_float(
                env, f"{SCHEDULER_ENV_PREFIX}STALE_AFTER_SECONDS", 60.0
            ),
            recover_limit=_positive_int(env, f"{SCHEDULER_ENV_PREFIX}RECOVER_LIMIT", 1000),
            liveness_path=default_liveness_path(dict(env)),
        )


__all__ = [
    "CONTAINER_ID_ENV",
    "PLACEHOLDER_ENV",
    "SchedulerRuntimeConfig",
    "WorkerConfigError",
    "WorkerRuntimeConfig",
    "instance_version",
    "placeholder_requested",
]
