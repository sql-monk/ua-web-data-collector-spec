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
  враховуватись у heartbeat-derived capacity (§7.6).

Доменне планування (розклади джерел, enqueue discovery-jobs) підключається через параметр
`tick` і належить іншим WP.

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
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from collector.core.logging import get_logger
from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.errors import PersistenceError
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.advisory import AdvisoryLease
from collector.workers.config import SchedulerRuntimeConfig
from collector.workers.liveness import LivenessMarker
from collector.workers.session import bounded_transaction
from collector.workers.signals import StopSignalHandlers, install_stop_signal_handlers

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SchedulerTick = Callable[["AsyncSession", datetime], Awaitable[None]]
"""Один прохід планування. **Мусить бути ідемпотентним** — див. докстрінг модуля (M-4)."""


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """Підсумок одного `run_maintenance`: скільки leases повернуто і instances позначено stale."""

    recovered_leases: int
    stale_instances: int


async def run_maintenance(
    session: AsyncSession, now: datetime, *, config: SchedulerRuntimeConfig
) -> MaintenanceResult:
    """Один maintenance-прохід: повернення прострочених lease + позначення stale instances."""
    recovered = await queue_repo.recover_expired_leases(
        session, limit=config.recover_limit, now=now
    )
    stale = await pools_repo.mark_stale_instances(
        session, heartbeat_ttl=timedelta(seconds=config.stale_after_seconds), now=now
    )
    return MaintenanceResult(recovered_leases=len(recovered), stale_instances=len(stale))


def make_maintenance_tick(config: SchedulerRuntimeConfig) -> SchedulerTick:
    """Тік за замовчуванням: `run_maintenance` з логуванням ненульових результатів."""
    log = get_logger("collector.scheduler.maintenance")

    async def tick(session: AsyncSession, now: datetime) -> None:
        result = await run_maintenance(session, now, config=config)
        if result.recovered_leases or result.stale_instances:
            log.info(
                "scheduler.maintenance",
                recovered_leases=result.recovered_leases,
                stale_instances=result.stale_instances,
            )

    return tick


class SchedulerRuntime:
    """Singleton scheduler: advisory lease + періодичний тік, поки lease наш."""

    def __init__(
        self,
        config: SchedulerRuntimeConfig,
        engine: AsyncEngine,
        sessions: async_sessionmaker[AsyncSession],
        *,
        tick: SchedulerTick | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.config = config
        self.lease = AdvisoryLease(engine, config.lease_name)
        self._sessions = sessions
        self._tick = tick if tick is not None else make_maintenance_tick(config)
        self._clock = clock
        self._log = get_logger("collector.scheduler").bind(lease=config.lease_name)
        self.liveness = LivenessMarker(config.liveness_path)
        self._stop = asyncio.Event()
        self._active = False
        self.activated = asyncio.Event()
        self.attempted = asyncio.Event()
        self.ticks = 0
        self.acquire_attempts = 0
        self.activations = 0
        self.lease_losses = 0

    @property
    def is_active(self) -> bool:
        """Чи є цей процес активним singleton-ом (тобто чи тримає він lease)."""
        return self._active

    def request_stop(self) -> None:
        """Попросити graceful stop (те саме, що SIGTERM): lease звільняється, тік не чекає TTL."""
        self._stop.set()

    async def run(self, *, stop: asyncio.Event | None = None, install_signals: bool = True) -> None:
        """Цикл «взяти lease → планувати → перевірити lease», поки не надійде зупинка.

        Lease звужує вікно перекриття до тривалості одного тіку, але не усуває його повністю:
        тік мусить лишатись ідемпотентним (контракт у докстрінгу модуля, M-4 код-рев'ю).

        `install_signals=False` — вбудований запуск (тести, кілька runtime в одному процесі):
        глобальні handlers сигналів не чіпаються.
        """
        if stop is not None:
            self._stop = stop
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
                elif not await self._still_active():
                    await self._idle(self.config.lease_retry_seconds)
                    continue
                await self._run_tick()
                await self._idle(self.config.tick_seconds)
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
        except (SQLAlchemyError, OSError) as exc:
            self._log.warning(
                "scheduler.lease_attempt_failed", error=f"{type(exc).__name__}: {exc}"[:300]
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

    async def _still_active(self) -> bool:
        if await self.lease.is_held():
            return True
        self._active = False
        self.activated.clear()
        self.lease_losses += 1
        # Планування припиняється негайно: іншу репліку вже міг активувати той самий lease.
        self._log.warning("scheduler.lease_lost", ticks=self.ticks)
        return False

    async def _run_tick(self) -> None:
        try:
            async with bounded_transaction(
                self._sessions, self.config.statement_timeout_ms
            ) as session:
                await self._tick(session, self._clock())
        except (SQLAlchemyError, OSError, PersistenceError) as exc:
            self._log.error("scheduler.tick_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            return
        self.ticks += 1

    async def _idle(self, timeout: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout)


__all__ = [
    "MaintenanceResult",
    "SchedulerRuntime",
    "SchedulerTick",
    "make_maintenance_tick",
    "run_maintenance",
]
