"""Дешевий liveness-маркер довгоживучого процесу (вимога 7 картки WP-01D PR1).

Проблема, яку це закриває: healthcheck кожного application-контейнера був
`python -m collector.api.health <deps>` — **повний старт інтерпретатора** на кожну пробу плюс
з'єднання з БД. На 2-ядерному runner-і зі стартом 17 контейнерів це коштувало 3–6 с CPU на
пробу і змусило підняти `timeout` до 15 с, а `start_period` — до 90 с (`x-healthcheck-budget`).

Рішення: цикл runtime сам оновлює mtime маленького файлу в tmpfs, а healthcheck читає лише
mtime (`stat`+`date` у shell, ~5 мс, без Python і без БД):

```sh
test $(( $(date +%s) - $(stat -c %Y /tmp/collector-runtime.alive) )) -lt 30
```

Що саме доводить свіжий маркер: **процес живий і його event loop не заблокований** — файл
оновлює сторож lease (`WorkerRuntime._watchdog_loop`) і цикл scheduler-а, тож синхронний
`handle()`, дедлок чи зависла корутина роблять контейнер unhealthy без жодного запиту в БД.

Що він навмисно **не** доводить — готовність залежностей (§7.5 «process + критична
dependency»): readiness лишається за `depends_on` (`service_healthy` для PostgreSQL /
`service_completed_successfully` для міграцій) і за `worker_instances.status`/`last_heartbeat_at`,
які бачить оператор на екрані Workers. Недоступна БД не має рестартувати worker-и: рестарт цього
не лікує, а self-fencing уже зупиняє claim і скасовує активні tasks.

Це **не стан** у сенсі §15/FR-031: файл лежить у tmpfs (RAM), ніколи не читається самим
runtime, не переживає рестарт контейнера і не впливає на жодне рішення — replacement replica
працює так само без нього. Помилка запису ніколи не валить процес: маркер один раз логує
`runtime.liveness_unavailable` і вимикається (тоді healthcheck просто позначить контейнер
unhealthy — чесно, бо він і справді нічого не знає про процес).
"""

from __future__ import annotations

import os
from pathlib import Path

from collector.core.logging import get_logger

DEFAULT_LIVENESS_FILENAME = "collector-runtime.alive"
LIVENESS_FILE_ENV = "COLLECTOR_WORKER_LIVENESS_FILE"


def default_liveness_path(environ: dict[str, str] | None = None) -> Path:
    """`$COLLECTOR_WORKER_LIVENESS_FILE` або `<TMPDIR>/collector-runtime.alive`.

    `TMPDIR` у образі — `/tmp`, змонтований як tmpfs (`docker-compose.yml`), тому маркер
    ніколи не торкається rootfs (read-only) і не переживає контейнер.
    """
    env = os.environ if environ is None else environ
    configured = env.get(LIVENESS_FILE_ENV, "").strip()
    if configured:
        return Path(configured)
    # S108 незастосовний: це не тимчасовий файл із передбачуваним іменем у спільній /tmp
    # хоста, а маркер усередині контейнера, де /tmp — приватний tmpfs (mode=1777, size=64m),
    # і в якому процес працює під власним uid 10001 (`docker-compose.yml`).
    return Path(env.get("TMPDIR", "/tmp")) / DEFAULT_LIVENESS_FILENAME  # noqa: S108


class LivenessMarker:
    """Файл-маркер, mtime якого оновлює живий цикл процесу; `path=None` — вимкнено."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._log = get_logger("collector.runtime.liveness")
        self._disabled = path is None
        self.refreshes = 0

    @property
    def enabled(self) -> bool:
        return not self._disabled

    def refresh(self) -> None:
        """Оновити mtime маркера. Будь-яка помилка вимикає маркер, але не процес."""
        if self._disabled or self.path is None:
            return
        try:
            self.path.touch()
        except OSError as exc:
            self._disabled = True
            self._log.warning(
                "runtime.liveness_unavailable",
                path=str(self.path),
                error=f"{type(exc).__name__}: {exc}",
                detail="healthcheck вважатиме контейнер unhealthy — це чесний сигнал",
            )
            return
        self.refreshes += 1

    def remove(self) -> None:
        """Прибрати маркер на штатному виході (ідемпотентно)."""
        if self.path is None:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover — tmpfs, права незмінні протягом життя контейнера
            self._log.warning("runtime.liveness_cleanup_failed", path=str(self.path))


__all__ = [
    "DEFAULT_LIVENESS_FILENAME",
    "LIVENESS_FILE_ENV",
    "LivenessMarker",
    "default_liveness_path",
]
