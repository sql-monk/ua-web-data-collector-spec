"""`SchedulerRuntime` — singleton-планувальник (§7.5, §7.6, вимога 6 картки WP-01D).

Активним є рівно один процес: право планувати дає session advisory lease
(`collector.workers.advisory`). Другий instance **не падає і не стає активним** — він циклічно
пробує взяти lease і стає активним лише тоді, коли перший його звільнив або помер. Перед кожним
тіком активний instance перевіряє lease на боці сервера: якщо його більше немає (розрив
з'єднання, `pg_terminate_backend`, kill процесу), планування припиняється негайно, і процес
повертається у режим очікування.

Тік за замовчуванням — maintenance черги і pool-ів, який не має права виконуватись двічі
паралельно:

- `recover_expired_leases` — прострочені lease повертаються у чергу (fault case SIGKILL,
  §7.5/§15: «replacement replica підхоплює expired lease»);
- `mark_stale_instances` — instances без heartbeat довше TTL стають `stale` і перестають
  враховуватись у heartbeat-derived capacity (§7.6);
- `recover_expired_projection_leases` (PR1c п.6) — те саме для `projection_tasks`.

Доменне планування підключається **доменними тіками** (PR1c п.6): статична мапа
`collector.workers.registry.DOMAIN_TICKS` з lazy import (`reconciler:schedule`,
`compactor:schedule` WP-01B, `publisher:tick` N-2 — вимкнений за замовчуванням). Кожен тік має
власний інтервал, власні транзакції (`TickContext.transaction()`) і стелю тривалості; виняток чи
timeout одного тіку логується як `scheduler.tick_failed` і не зупиняє решту та lease.

**Контракт тіку: він має бути ідемпотентним і безпечним при перекритті з тіком іншого
instance.** Lease перевіряється на lease-зʼєднанні, а сам тік виконується в окремій session із
pool-у, тому між перевіркою і commit-ом тіку lease теоретично може зникнути
(`pg_terminate_backend`, failover, мережевий поділ) — і другий scheduler, який його підхопить,
почне свій тік паралельно (M-4 код-рев'ю). Прив'язати тік до самого lease-зʼєднання не можна
без того, щоб тримати його `idle in transaction`, тому гарантія формулюється як контракт:

- дефолтний тік йому відповідає за побудовою — `recover_expired_leases` і
  `mark_stale_instances` працюють через `FOR UPDATE SKIP LOCKED` і повторний прохід нічого не
  змінює (тест-вартовий `test_maintenance_tick_is_safe_when_two_schedulers_overlap`);
- доменний тік **не має права** робити не-ідемпотентні дії без власного ключа ідемпотентності:
  `enqueue` з `idempotency_key`, що містить дискримінатор циклу (§9.3 п.3), — так, «вставити
  рядок і сподіватись на lease» — ні.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING

from collector.core.logging import get_logger
from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.workers.advisory import AdvisoryLease
from collector.workers.backends import CRAWL_JOBS, PROJECTION_TASKS
from collector.workers.config import SchedulerRuntimeConfig
from collector.workers.handlers import redact
from collector.workers.liveness import LivenessMarker
from collector.workers.login import verify_component_login
from collector.workers.registry import LoadedTick, load_domain_ticks
from collector.workers.roles import SCHEDULER_DB_ROLE
from collector.workers.session import bounded_transaction
from collector.workers.signals import StopSignalHandlers, install_stop_signal_handlers

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SchedulerTick = Callable[["AsyncSession", datetime], Awaitable[None]]
"""Вбудований maintenance-тік: одна транзакція. **Мусить бути ідемпотентним** (M-4)."""

MAINTENANCE_TICK = "maintenance"
MAINTENANCE_TIMEOUT_FLOOR_SECONDS = 30.0
"""Мінімальна стеля одного maintenance-проходу (client-side `wait_for`)."""


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """Підсумок одного `run_maintenance`: повернені leases (обидві черги) і stale instances."""

    recovered_leases: int
    stale_instances: int
    recovered_projection_leases: int = 0


async def run_maintenance(
    session: AsyncSession, now: datetime, *, config: SchedulerRuntimeConfig
) -> MaintenanceResult:
    """Один maintenance-прохід: прострочені lease `crawl_jobs` і `projection_tasks` — у чергу,
    instances без heartbeat — `stale`.

    `recover_expired_projection_leases` — вбудований тік WP-01D (PR1c п.6): роль
    `collector_scheduler` має column UPDATE на `projection_tasks` (`roles.sql`), а без нього
    task убитого projector-а лишалась би `leased` назавжди.
    """
    recovered = await CRAWL_JOBS.recover_expired(session, limit=config.recover_limit, now=now)
    projection = await PROJECTION_TASKS.recover_expired(
        session, limit=config.recover_limit, now=now
    )
    stale = await pools_repo.mark_stale_instances(
        session, heartbeat_ttl=timedelta(seconds=config.stale_after_seconds), now=now
    )
    return MaintenanceResult(
        recovered_leases=len(recovered),
        stale_instances=len(stale),
        recovered_projection_leases=len(projection),
    )


def make_maintenance_tick(config: SchedulerRuntimeConfig) -> SchedulerTick:
    """Тік за замовчуванням: `run_maintenance` з логуванням ненульових результатів."""
    log = get_logger("collector.scheduler.maintenance")

    async def tick(session: AsyncSession, now: datetime) -> None:
        result = await run_maintenance(session, now, config=config)
        if result.recovered_leases or result.stale_instances or result.recovered_projection_leases:
            log.info(
                "scheduler.maintenance",
                recovered_leases=result.recovered_leases,
                recovered_projection_leases=result.recovered_projection_leases,
                stale_instances=result.stale_instances,
            )

    return tick


@dataclass(frozen=True, slots=True)
class TickContext:
    """Що отримує доменний тік `async def tick(ctx: TickContext)` (PR1c п.6).

    Тік сам відкриває свої транзакції (`ctx.transaction()` — з `statement_timeout`, як усі
    транзакції runtime): publisher робить коротку транзакцію, доставку поза транзакцією і ще одну
    коротку транзакцію, тож «одна транзакція на тік» йому не підходить. `lease_is_ours()` —
    серверна перевірка singleton-lease перед кожним не-ідемпотентним кроком довгого тіку.
    Контракт ідемпотентності (докстрінг модуля, `docs/workers.md` §6) діє повністю.
    """

    name: str
    sessions: async_sessionmaker[AsyncSession]
    now: datetime
    statement_timeout_ms: int
    lease_is_ours: Callable[[], Awaitable[bool]]
    env: Mapping[str, str]

    def transaction(self) -> AbstractAsyncContextManager[AsyncSession]:
        return bounded_transaction(self.sessions, self.statement_timeout_ms)


@dataclass(slots=True)
class _Scheduled:
    name: str
    interval_seconds: float
    timeout_seconds: float
    run: Callable[[], Awaitable[None]]
    next_due: float = 0.0


class SchedulerRuntime:
    """Singleton scheduler: advisory lease + композиція ізольованих тіків, поки lease наш."""

    def __init__(
        self,
        config: SchedulerRuntimeConfig,
        engine: AsyncEngine,
        sessions: async_sessionmaker[AsyncSession],
        *,
        tick: SchedulerTick | None = None,
        domain_ticks: Sequence[LoadedTick] | None = None,
        clock: Callable[[], datetime] = utcnow,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """`tick` — заміна вбудованого maintenance-тіку (тести); `domain_ticks=None` — доменні
        тіки з реєстру (`collector.workers.registry.DOMAIN_TICKS`, lazy import). Зламаний
        доменний модуль → `HandlerRegistryError` тут, до спроби взяти lease."""
        self.config = config
        self.lease = AdvisoryLease(engine, config.lease_name)
        self._sessions = sessions
        self._clock = clock
        self._env: Mapping[str, str] = os.environ if environ is None else environ
        self._log = get_logger("collector.scheduler").bind(lease=config.lease_name)
        self.liveness = LivenessMarker(config.liveness_path)
        self._stop = asyncio.Event()
        self._active = False
        # Серверна перевірка lease іде одним з'єднанням: цикл і `lease_is_ours()` тіку не мають
        # виконувати запит на ньому одночасно (asyncpg: another operation is in progress).
        self._lease_check = asyncio.Lock()
        maintenance = tick if tick is not None else make_maintenance_tick(config)
        loaded = load_domain_ticks(self._env) if domain_ticks is None else tuple(domain_ticks)

        async def run_maintenance_tick() -> None:
            async with bounded_transaction(
                self._sessions, self.config.statement_timeout_ms
            ) as session:
                await maintenance(session, self._clock())

        self._ticks: list[_Scheduled] = [
            _Scheduled(
                name=MAINTENANCE_TICK,
                interval_seconds=config.tick_seconds,
                # `statement_timeout` вже обмежує кожен запит тіку; стеля всього проходу лише не
                # дає завислому з'єднанню тримати цикл вічно. Нижня межа 30 с: до PR1c стелі не
                # було взагалі, а тісна стеля на завантаженій машині скасовувала б здоровий тік.
                timeout_seconds=max(
                    config.statement_timeout_ms / 1000 * 3,
                    config.tick_seconds,
                    MAINTENANCE_TIMEOUT_FLOOR_SECONDS,
                ),
                run=run_maintenance_tick,
            ),
            *(self._schedule_domain(item) for item in loaded),
        ]
        self.activated = asyncio.Event()
        self.attempted = asyncio.Event()
        self.ticks = 0
        self.tick_runs: dict[str, int] = {}
        self.tick_failures: dict[str, int] = {}
        self.acquire_attempts = 0
        self.activations = 0
        self.lease_losses = 0

    @property
    def is_active(self) -> bool:
        """Чи є цей процес активним singleton-ом (тобто чи тримає він lease)."""
        return self._active

    @property
    def tick_names(self) -> tuple[str, ...]:
        """Імена тіків у порядку виконання (maintenance — першим)."""
        return tuple(item.name for item in self._ticks)

    def request_stop(self) -> None:
        """Попросити graceful stop (те саме, що SIGTERM): lease звільняється, тік не чекає TTL."""
        self._stop.set()

    async def run(self, *, stop: asyncio.Event | None = None, install_signals: bool = True) -> None:
        """Цикл «взяти lease → виконати тіки, що настали → перевірити lease», поки не зупинка.

        Lease звужує вікно перекриття до тривалості одного тіку, але не усуває його повністю:
        тік мусить лишатись ідемпотентним (контракт у докстрінгу модуля, M-4 код-рев'ю).

        `install_signals=False` — вбудований запуск (тести, кілька runtime в одному процесі):
        глобальні handlers сигналів не чіпаються.

        Першим кроком — перевірка LOGIN-ролі (§13): scheduler працює лише під
        `collector_scheduler`; інакше `RoleLoginError` ще до спроби взяти lease.
        """
        if stop is not None:
            self._stop = stop
        db_role = await verify_component_login(
            self._sessions, SCHEDULER_DB_ROLE, statement_timeout_ms=self.config.statement_timeout_ms
        )
        self._log.info("scheduler.db_login", db_role=db_role, ticks=list(self.tick_names))
        signals = (
            install_stop_signal_handlers(self._on_signal)
            if install_signals
            else StopSignalHandlers()
        )
        try:
            while not self._stop.is_set():
                # Liveness — те саме, що у worker-а: доводить живий цикл, не чіпаючи БД.
                self.liveness.refresh()
                if not self._active:
                    if not await self._try_activate():
                        await self._idle(self.config.lease_retry_seconds)
                        continue
                    # Новий активний singleton одразу виконує всі тіки (як і до PR1c).
                    for item in self._ticks:
                        item.next_due = 0.0
                elif not await self._still_active():
                    await self._idle(self.config.lease_retry_seconds)
                    continue
                await self._run_due_ticks()
                await self._idle(self._until_next_due())
        finally:
            self._active = False
            self.activated.clear()
            self.liveness.remove()
            await self.lease.release()
            signals.restore()
            self._log.info("scheduler.stopped", ticks=self.ticks, activations=self.activations)

    def _on_signal(self, signum: object) -> None:
        self._log.info("scheduler.stop_requested", signal=str(signum))
        self.request_stop()

    async def _try_activate(self) -> bool:
        self.acquire_attempts += 1
        try:
            acquired = await self.lease.try_acquire()
        except Exception as exc:  # noqa: BLE001 — будь-яка відмова = «не активний», повтор пізніше
            self._log.warning(
                "scheduler.lease_attempt_failed",
                error=redact(f"{type(exc).__name__}: {exc}")[:300],
            )
            acquired = False
        self.attempted.set()
        if not acquired:
            return False
        self._active = True
        self.activations += 1
        self.activated.set()
        self._log.info("scheduler.activated", backend_pid=self.lease.backend_pid)
        return True

    async def _lease_held(self) -> bool:
        """Серверна перевірка lease під lock-ом; `shield` — timeout тіку не рве запит навпіл."""
        async with self._lease_check:
            return await asyncio.shield(self.lease.is_held())

    async def _still_active(self) -> bool:
        if await self._lease_held():
            return True
        self._active = False
        self.activated.clear()
        self.lease_losses += 1
        # Планування припиняється негайно: іншу репліку вже міг активувати той самий lease.
        self._log.warning("scheduler.lease_lost", ticks=self.ticks)
        return False

    async def _run_due_ticks(self) -> None:
        """Тіки, чий час настав, по черзі; кожен ізольований від інших.

        Виняток або timeout одного тіку логується (`scheduler.tick_failed`, `tick=<name>`) і не
        зупиняє решту тіків і не відпускає lease. Перед кожним доменним тіком lease
        перевіряється ще раз: lease, втрачений посеред проходу, зупиняє і решту тіків.
        """
        for item in self._ticks:
            if self._stop.is_set() or not self._active:
                return
            if monotonic() < item.next_due:
                continue
            if item.name != MAINTENANCE_TICK and not await self._still_active():
                return
            try:
                await asyncio.wait_for(item.run(), timeout=item.timeout_seconds)
            except Exception as exc:  # noqa: BLE001 — ізоляція: помилка одного тіку не зупиняє решту
                self.tick_failures[item.name] = self.tick_failures.get(item.name, 0) + 1
                self._log.error(
                    "scheduler.tick_failed",
                    tick=item.name,
                    error=redact(f"{type(exc).__name__}: {exc}")[:300],
                )
            else:
                self.tick_runs[item.name] = self.tick_runs.get(item.name, 0) + 1
                if item.name == MAINTENANCE_TICK:
                    self.ticks += 1
            item.next_due = monotonic() + item.interval_seconds

    def _until_next_due(self) -> float:
        """Пауза до найближчого тіку, але не довша за `tick_seconds` (перевірка lease/liveness)."""
        soonest = min(item.next_due for item in self._ticks)
        return min(max(soonest - monotonic(), 0.0), self.config.tick_seconds)

    def _schedule_domain(self, loaded: LoadedTick) -> _Scheduled:
        spec = loaded.spec

        async def run() -> None:
            await loaded.run(
                TickContext(
                    name=spec.name,
                    sessions=self._sessions,
                    now=self._clock(),
                    statement_timeout_ms=self.config.statement_timeout_ms,
                    lease_is_ours=self._lease_held,
                    env=self._env,
                )
            )

        return _Scheduled(
            name=spec.name,
            interval_seconds=spec.interval_seconds,
            timeout_seconds=spec.timeout_seconds,
            run=run,
        )

    async def _idle(self, timeout: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout)


__all__ = [
    "MAINTENANCE_TICK",
    "MaintenanceResult",
    "SchedulerRuntime",
    "SchedulerTick",
    "TickContext",
    "make_maintenance_tick",
    "run_maintenance",
]
