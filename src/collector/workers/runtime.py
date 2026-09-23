"""`WorkerRuntime` — stateless runtime-каркас worker pool (§7.6, FR-031, FR-032, R-52).

Життєвий цикл процесу:

```text
boot → verify DB login (§13) → register(starting) → readiness → ready ⇄ claim/handle/heartbeat
                                          │
                       SIGTERM / drain barrier ▼
                                       draining → (активні tasks дотягуються
                                       у межах stop_grace_period) → stopped → exit 0
```

Інваріанти:

- **жодного стану на локальному диску** (§15): `worker_instance_id` — UUIDv7, згенерований на
  boot; усе, що переживає рестарт, лежить у PostgreSQL. Docker hostname пишеться лише як
  metadata `worker_instances.hostname` і ні на що не впливає;
- **lease належить instance**: claim/heartbeat/complete/retry ідуть із `lease_owner =
  str(instance_id)`; heartbeat чужої job-и відхиляє сам репозиторій
  (`LeaseNotOwnedError`), і runtime негайно скасовує локальний task — після
  `recover_expired_leases` job уже може виконувати інший instance;
- **desired concurrency живе в БД**: кожен heartbeat перечитує `worker_pools`, тому зміна
  concurrency застосовується без рестарту — нові слоти відкриваються одразу, зайві просто
  більше не claim-ляться після завершення активних tasks (§7.6);
- **drain не покладається на вибір контейнера orchestrator-ом** (R-57): крім SIGTERM, claim
  зупиняє і `drain_requested_at` у власному рядку `worker_instances`, який ставить
  role-wide барʼєр контролера (PR3). Барʼєр перевіряється і в самій транзакції claim під
  `FOR SHARE`, тож після коміту `mark_draining` жоден claim цього instance нової job не візьме;
- **власна LOGIN-роль БД** (§13): перший запит `_boot` — `verify_component_login`; superuser,
  член `collector_migrate` чи роль чужого компонента → `RoleLoginError` до реєстрації і claim;
- **SIGKILL — fault case**: при скасуванні (`asyncio.CancelledError`) runtime не повертає
  leases і не пише `stopped` — саме так поводиться вбитий контейнер; lease підбирає
  `recover_expired_leases` іншого instance після експірації.

Транзакційні межі: claim — окрема транзакція (row locks звільняються одразу після commit);
звіт про кожну task — окрема транзакція; heartbeat instance + продовження leases активних
tasks — одна транзакція на тік.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from collector.contracts import new_entity_id
from collector.core.logging import get_logger
from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.errors import (
    ConflictError,
    InvalidTransitionError,
    LeaseNotOwnedError,
    PersistenceError,
)
from collector.persistence.postgres.models import WorkerInstance
from collector.persistence.postgres.repositories import pools as pools_repo
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import (
    Task,
    TaskHandler,
    TaskResult,
    check_handler_contract,
    redact,
    resolve_handler,
    result_for_exception,
)
from collector.workers.liveness import LivenessMarker
from collector.workers.login import verify_component_login
from collector.workers.roles import WorkerRole, db_role_for, default_pool_spec
from collector.workers.session import bounded_transaction
from collector.workers.signals import StopSignalHandlers, install_stop_signal_handlers

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from collector.persistence.postgres.models import CrawlJob

BOOTSTRAP_REASON = "bootstrap default pool (§7.6)"
READY_RETRY_ATTEMPTS = 5
READY_RETRY_BASE_SECONDS = 0.5
MAX_CLAIM_BACKOFF_SECONDS = 30.0


class WorkerRuntimeError(RuntimeError):
    """Runtime не може працювати коректно і має завершитись (boot не вдався тощо)."""


def _task_from_job(job: CrawlJob) -> Task:
    return Task(
        job_id=job.job_id,
        job_type=job.job_type,
        args=dict(job.args),
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        priority=job.priority,
        not_before=job.not_before,
        run_id=job.run_id,
        source_id=job.source_id,
    )


@dataclass(slots=True)
class _ActiveTask:
    task: Task
    handle: asyncio.Task[None]


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    """Прочитаний desired state pool: те, що runtime підтверджує у heartbeat."""

    desired_concurrency: int
    revision: int


class WorkerRuntime:
    """Один worker-процес ролі: claim-loop, lease heartbeat, drain, реєстрація instance."""

    def __init__(
        self,
        config: WorkerRuntimeConfig,
        sessions: async_sessionmaker[AsyncSession],
        handler: TaskHandler | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.config = config
        self.instance_id = new_entity_id()
        self.handler = handler if handler is not None else resolve_handler(config.role)
        check_handler_contract(self.handler)
        self._sessions = sessions
        self._clock = clock
        self._log = get_logger(f"collector.worker.{config.role.value}").bind(
            instance_id=str(self.instance_id), role=config.role.value
        )
        self._active: dict[UUID, _ActiveTask] = {}
        # Скасовані (fencing/втрата lease) tasks: їх треба дочекатись перед виходом, інакше
        # доменний handler лишається з незакритим з'єднанням (L-3 код-рев'ю).
        self._cancelled: list[asyncio.Task[None]] = []
        self._claim_failures = 0
        self._claim_backoff = 0.0
        self.liveness = LivenessMarker(config.liveness_path)
        self._pool = PoolSnapshot(
            desired_concurrency=default_pool_spec(config.role).desired_concurrency, revision=0
        )
        self._status = "starting"
        self._drain_barrier = False
        self._stop = asyncio.Event()
        self._wakeup = asyncio.Event()
        # Момент останнього ПІДТВЕРДЖЕНОГО базою heartbeat — база self-fencing (§7.6 lease).
        self._last_heartbeat_ok = monotonic()
        self._fenced = False
        self.heartbeats = 0
        self.lost_leases = 0
        self.fences = 0

    # --- стан для тестів і логів -------------------------------------------------------------

    @property
    def owner(self) -> str:
        """`crawl_jobs.lease_owner` цього instance."""
        return str(self.instance_id)

    @property
    def status(self) -> str:
        """Локальний стан (`starting | ready | draining | stopped`), дзеркало `worker_instances`."""
        return self._status

    @property
    def desired_concurrency(self) -> int:
        """Останнє прочитане з `worker_pools` значення (джерело істини — БД)."""
        return self._pool.desired_concurrency

    @property
    def pool_revision(self) -> int:
        return self._pool.revision

    @property
    def active_tasks(self) -> int:
        return len(self._active)

    @property
    def effective_concurrency(self) -> int:
        """Скільки слотів процес реально відкриває: desired з БД, обрізаний стелею pool-у (M-3)."""
        return min(self._pool.desired_concurrency, self.config.max_slots)

    @property
    def fenced(self) -> bool:
        """Self-fencing: lease вважається втраченим, бо heartbeat не підтверджується базою.

        Поки прапорець піднятий, instance не має жодної активної task і не бере нових —
        роботу цих jobs уже міг перехопити інший instance після `recover_expired_leases`.
        """
        return self._fenced

    @property
    def claiming(self) -> bool:
        """Чи бере runtime нові jobs (drain барʼєр, self-fencing або зупинка знімають claim).

        `_drain_barrier` — це барʼєр **цього** instance (`worker_instances.drain_requested_at`).
        Роль зупиняється, коли барʼєр поставлено кожному її instance; робить це `PoolController`
        (PR3) — тут лише примітив, на якому він будується (R-57: рішення не залежить від того,
        який контейнер видалить Compose/Swarm).
        """
        return (
            not self._stop.is_set()
            and not self._drain_barrier
            and not self._fenced
            and self._status == "ready"
        )

    def request_stop(self) -> None:
        """Попросити graceful drain (те саме, що SIGTERM); безпечно з будь-якого місця loop-у."""
        self._stop.set()
        self._wakeup.set()

    # --- головний цикл -----------------------------------------------------------------------

    async def run(self, *, stop: asyncio.Event | None = None, install_signals: bool = True) -> None:
        """Повний життєвий цикл процесу; повертає керування після drain (exit code 0).

        `stop` — зовнішня подія зупинки (тести, вбудований запуск); якщо не задано, зупинку
        дає SIGTERM/SIGINT. `install_signals=False` не чіпає глобальні handlers процесу — це
        режим вбудованого запуску (кілька runtime в одному процесі, pytest). Скасування самої
        корутини = SIGKILL-сценарій: leases лишаються простроченими для
        `recover_expired_leases`.
        """
        if stop is not None:
            self._stop = stop
        signals = (
            install_stop_signal_handlers(self._on_signal)
            if install_signals
            else StopSignalHandlers()
        )
        background: list[asyncio.Task[None]] = []
        try:
            await self._boot()
            background = [
                asyncio.create_task(
                    self._heartbeat_loop(), name=f"worker-heartbeat-{self.instance_id}"
                ),
                # Окрема задача навмисно: якщо heartbeat завис у драйвері, він сам себе не
                # перевірить (H-1 код-рев'ю). Watchdog рахує ЧАС від останнього підтвердженого
                # heartbeat і не залежить від того, чи повернулась heartbeat-корутина.
                asyncio.create_task(
                    self._watchdog_loop(), name=f"worker-watchdog-{self.instance_id}"
                ),
            ]
            await self._claim_loop()
            await self._drain()
        except asyncio.CancelledError:
            self._log.warning("worker.cancelled", active_tasks=len(self._active))
            raise
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with suppress(asyncio.CancelledError):
                    await task
            await self._await_cancelled_tasks()
            self.liveness.remove()
            signals.restore()

    def _on_signal(self, signum: object) -> None:
        self._log.info("worker.stop_requested", signal=str(signum))
        self.request_stop()

    async def _boot(self) -> None:
        # §13: жодного запису (bootstrap pool, реєстрація) і жодного claim під чужою роллю.
        db_role = await verify_component_login(
            self._sessions,
            db_role_for(self.config.role),
            statement_timeout_ms=self.config.statement_timeout_ms,
        )
        self._log.info("worker.db_login", db_role=db_role)
        self._pool = await self._ensure_pool()
        async with self._transaction() as session:
            await pools_repo.register_instance(
                session,
                self.instance_id,
                self.config.role,
                version=self.config.version,
                slots_total=self._pool.desired_concurrency,
                deployment=self.config.deployment,
                container_id=self.config.container_id,
                hostname=self.config.hostname,
                pool_revision=self._pool.revision,
                now=self._now(),
            )
        self._log.info(
            "worker.registered",
            status="starting",
            desired_concurrency=self._pool.desired_concurrency,
            pool_revision=self._pool.revision,
            job_types=list(self.handler.job_types),
        )
        await self._check_ready()
        await self._become_ready()
        # Відлік self-fencing починається від підтвердженої реєстрації, а не від створення
        # обʼєкта: повільний boot не має виглядати як втрачений lease.
        self._last_heartbeat_ok = monotonic()
        self.liveness.refresh()

    async def _become_ready(self) -> None:
        """Перехід `starting → ready` з повторами; невдача = процес не піднявся (M-1 код-рев'ю).

        Одна проковтнута помилка на boot робила worker «живим, але німим»: heartbeat ішов,
        `mark_stale_instances` такий instance не позначав, healthcheck контейнера бачив живий
        PostgreSQL — а claim не починався ніколи. Тепер перехід повторюється з backoff, а якщо
        не вдався — виняток піднімається з `run()`: контейнер падає і його перезапускає Docker
        (видимо), а рядок `worker_instances` лишається `starting` і застаріває.
        """
        delay = READY_RETRY_BASE_SECONDS
        for attempt in range(1, READY_RETRY_ATTEMPTS + 1):
            if await self._set_status("ready"):
                return
            if attempt == READY_RETRY_ATTEMPTS:
                break
            self._log.warning("worker.ready_retry", attempt=attempt, delay_seconds=delay)
            await asyncio.sleep(delay)
            delay *= 2
        msg = (
            f"instance {self.instance_id}: не вдалося перейти у ready за "
            f"{READY_RETRY_ATTEMPTS} спроб — процес завершується, щоб оркестратор перезапустив "
            "репліку замість живого, але німого worker-а"
        )
        raise WorkerRuntimeError(msg)

    async def _ensure_pool(self) -> PoolSnapshot:
        """Прочитати desired state ролі; на чистій БД створити pool із defaults §7.6."""
        async with self._transaction() as session:
            pool = await pools_repo.get_pool(session, self.config.role)
            if pool is not None:
                return PoolSnapshot(pool.desired_concurrency, pool.revision)
        spec = default_pool_spec(self.config.role)
        state = pools_repo.PoolDesiredState(
            desired_replicas=spec.desired_replicas,
            desired_concurrency=spec.desired_concurrency,
            min_replicas=spec.min_replicas,
            max_replicas=spec.max_replicas,
            resource_profile=spec.resource_profile,
        )
        try:
            async with self._transaction() as session:
                created = await pools_repo.upsert_pool(
                    session,
                    self.config.role,
                    state,
                    actor=f"worker:{self.instance_id}",
                    reason=BOOTSTRAP_REASON,
                    expected_revision=None,
                    now=self._now(),
                )
                return PoolSnapshot(created.desired_concurrency, created.revision)
        except (ConflictError, IntegrityError):
            # Інша репліка ролі створила pool одночасно — desired state уже є, читаємо його.
            async with self._transaction() as session:
                # Нижня межа для зависань на боці сервера: без неї запит у «чорну діру» чекає
                # до TCP RTO ядра (десятки хвилин), а не до вікна fencing (H-1).
                pool = await pools_repo.get_pool(session, self.config.role)
            if pool is None:  # pragma: no cover — можливо лише при видаленні pool під час boot
                msg = f"worker pool {self.config.role.value!r} зник під час реєстрації"
                raise ConflictError(msg) from None
            return PoolSnapshot(pool.desired_concurrency, pool.revision)

    def _transaction(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Транзакція runtime із `statement_timeout` (S-4): жоден запит не переживає fencing."""
        return bounded_transaction(self._sessions, self.config.statement_timeout_ms)

    async def _check_ready(self) -> None:
        """Readiness §7.5: БД відповідає і доменні залежності ролі готові."""
        async with self._sessions() as session:
            await session.execute(text("SELECT 1"))
        await self.handler.check_ready()

    async def _claim_loop(self) -> None:
        while not self._stop.is_set():
            if not self.claiming:
                await self._idle(self.config.poll_seconds)
                continue
            free = self.effective_concurrency - len(self._active)
            claimed = 0
            if free > 0:
                claimed = await self._claim(min(free, self.config.claim_batch))
            if claimed == 0:
                await self._idle(max(self.config.poll_seconds, self._claim_backoff))
            else:
                await asyncio.sleep(0)

    async def _claim(self, limit: int) -> int:
        try:
            async with self._transaction() as session:
                if not await self._claim_allowed(session):
                    return 0
                jobs = await queue_repo.claim(
                    session,
                    self.handler.job_types,
                    self.owner,
                    self.config.lease_seconds,
                    limit=limit,
                    now=self._now(),
                )
                tasks = [_task_from_job(job) for job in jobs]
        except (SQLAlchemyError, OSError, PersistenceError) as exc:
            # Backoff, щоб мертву базу не опитував кожен worker раз на секунду (L-7 код-рев'ю).
            self._claim_failures += 1
            self._claim_backoff = min(
                self.config.poll_seconds * 2**self._claim_failures, MAX_CLAIM_BACKOFF_SECONDS
            )
            self._log.warning(
                "worker.claim_failed",
                error=redact(f"{type(exc).__name__}: {exc}"),
                next_attempt_in=round(self._claim_backoff, 3),
            )
            return 0
        self._claim_failures = 0
        self._claim_backoff = 0.0
        if self._fenced:
            # Fence піднявся, поки claim був у базі. Запуск цих tasks означав би роботу без
            # підтвердженого lease, яку сторож уже не скасує (він спрацьовує раз на fence):
            # lease лишається спливати і job повертає `recover_expired_leases`.
            for task in tasks:
                self.lost_leases += 1
                self._log.warning(
                    "worker.lease_left_to_expire",
                    job_id=str(task.job_id),
                    reason="claimed while fenced",
                    lease_seconds=self.config.lease_seconds,
                )
            return 0
        for task in tasks:
            self._start(task)
        if tasks:
            self._log.info("worker.claimed", count=len(tasks), active_tasks=len(self._active))
        return len(tasks)

    async def _claim_allowed(self, session: AsyncSession) -> bool:
        """Перевірити барʼєр drain у власному рядку instance **в тій самій транзакції**, що й claim.

        Локальний `claiming` читається до claim, а барʼєр runtime дізнається лише з heartbeat.
        Без цієї перевірки claim, що вже пішов у базу, міг узяти job, покладену в чергу ПІСЛЯ
        того, як `mark_draining`/`mark_stopped` закомітився і heartbeat його застосував
        (флейки scaling-тестів під навантаженням). `FOR SHARE` конфліктує з `FOR UPDATE`, яким
        `set_instance_status` блокує рядок, тож перехід у `draining`/`stopped` чекає, доки
        claim цього instance закомітиться: коміт барʼєра = жодного claim, який його не бачив.
        """
        row = (
            await session.execute(
                select(WorkerInstance.status, WorkerInstance.drain_requested_at)
                .where(WorkerInstance.instance_id == self.instance_id)
                .with_for_update(read=True)
            )
        ).one_or_none()
        if row is None:
            return False
        if row.drain_requested_at is not None:
            self._apply_drain_barrier(True)
            return False
        return bool(row.status == "ready")

    def _start(self, task: Task) -> None:
        handle = asyncio.create_task(self._execute(task), name=f"worker-task-{task.job_id}")
        self._active[task.job_id] = _ActiveTask(task=task, handle=handle)

    async def _execute(self, task: Task) -> None:
        cancelled = False
        try:
            try:
                result = await self.handler.handle(task)
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as exc:  # noqa: BLE001 — будь-яка помилка handler-а стає TaskResult
                self._log.warning(
                    "worker.task_failed",
                    job_id=str(task.job_id),
                    error=redact(f"{type(exc).__name__}: {exc}")[:300],
                )
                result = result_for_exception(exc)
            await self._report(task, result)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if not cancelled:
                # Скасована task лишається в `_active`, щоб drain повернув її lease.
                self._active.pop(task.job_id, None)
            self._wakeup.set()

    async def _report(self, task: Task, result: TaskResult) -> None:
        now = self._now()
        try:
            async with self._transaction() as session:
                if result.disposition == "complete":
                    await queue_repo.complete(session, task.job_id, self.owner, now=now)
                elif result.disposition == "retry":
                    await queue_repo.retry(
                        session,
                        task.job_id,
                        self.owner,
                        error_code=result.error_code or "unknown",
                        error_message=result.error_message,
                        now=now,
                    )
                else:
                    await queue_repo.quarantine(
                        session,
                        task.job_id,
                        self.owner,
                        error_code=result.error_code or "unknown",
                        error_message=result.error_message,
                        now=now,
                    )
        except LeaseNotOwnedError:
            # Lease забрав `recover_expired_leases` (або оператор) — результат уже не наш.
            self.lost_leases += 1
            self._log.warning("worker.lease_lost", job_id=str(task.job_id), phase="report")
        except (SQLAlchemyError, OSError, PersistenceError) as exc:
            # Будь-яка інша помилка persistence (`ConflictError`, `InvalidTransitionError`, …)
            # раніше вилітала з task і зникала у GC без жодного рядка в логах (L-4 код-рев'ю).
            self._log.error(
                "worker.report_failed",
                job_id=str(task.job_id),
                error=redact(f"{type(exc).__name__}: {exc}"),
            )
        else:
            self._log.info(
                "worker.task_done", job_id=str(task.job_id), disposition=result.disposition
            )

    # --- heartbeat ---------------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Тіки за дедлайном (а не `sleep` після роботи) і з бюджетом на кожен тік.

        `sleep(interval)` після тіку давав період `interval + тривалість тіку`: на повільній базі
        дрейф накопичувався саме тоді, коли запас до експірації lease потрібен найбільше (L-1).
        Бюджет `wait_for` відрізає зависання в драйвері: тік, який не вклався, скасовується, і
        перевірка вікна fencing виконується негайно (H-1).
        """
        deadline = monotonic()
        budget = self.config.heartbeat_tick_budget
        while True:
            deadline += self.config.heartbeat_seconds
            await asyncio.sleep(max(deadline - monotonic(), 0.0))
            try:
                await asyncio.wait_for(self._heartbeat(), timeout=budget)
            except TimeoutError:
                self._log.warning("worker.heartbeat_timeout", budget_seconds=budget)
                self._fence_if_lease_unconfirmed()

    async def _watchdog_loop(self) -> None:
        """Сторож lease: незалежно від heartbeat-циклу стежить за часом без підтвердження.

        Друге призначення — помітити заблокований event loop (синхронний `handle`, M-2): якщо
        власний `sleep` сторожа прокинувся значно пізніше, ніж мав, у логах зʼявляється
        `worker.event_loop_stalled` — інакше така затримка виглядала б як проблема бази.
        """
        interval = self.config.watchdog_interval
        while True:
            expected = monotonic() + interval
            await asyncio.sleep(interval)
            # Маркер оновлює саме сторож: він доводить, що event loop живий і не заблокований
            # (вимога 7 картки). Жодного запиту в БД проба не робить.
            self.liveness.refresh()
            lag = monotonic() - expected
            if lag > max(interval, self.config.heartbeat_seconds):
                self._log.warning(
                    "worker.event_loop_stalled",
                    lag_seconds=round(lag, 3),
                    detail="handle() має бути неблокуючим: CPU-bound роботу — в asyncio.to_thread",
                )
            self._fence_if_lease_unconfirmed()

    async def _heartbeat(self) -> None:
        """Один тік: instance heartbeat + продовження lease активних tasks + читання pool."""
        now = self._now()
        lost: list[UUID] = []
        active = list(self._active)
        snapshot = self._pool
        drain_requested = self._drain_barrier
        try:
            async with self._transaction() as session:
                # Нижня межа для зависань на боці сервера: без неї запит у «чорну діру» чекає
                # до TCP RTO ядра (десятки хвилин), а не до вікна fencing (H-1).
                pool = await pools_repo.get_pool(session, self.config.role)
                snapshot = (
                    PoolSnapshot(pool.desired_concurrency, pool.revision)
                    if pool is not None
                    else self._pool
                )
                instance = await pools_repo.heartbeat_instance(
                    session,
                    self.instance_id,
                    # Саме стільки слотів репліка справді здатна тримати (M-3): heartbeat-derived
                    # capacity не має обіцяти контролеру більше, ніж є.
                    slots_total=min(snapshot.desired_concurrency, self.config.max_slots),
                    slots_active=len(active),
                    active_leases=len(active),
                    pool_revision=snapshot.revision,
                    now=now,
                )
                drain_requested = instance.drain_requested_at is not None
                for job_id in active:
                    try:
                        await queue_repo.heartbeat(
                            session, job_id, self.owner, self.config.lease_seconds, now=now
                        )
                    except LeaseNotOwnedError:
                        lost.append(job_id)
        except InvalidTransitionError:
            # Instance уже позначений `stopped` (оператор/контролер) — процес має завершитись.
            self._log.warning("worker.heartbeat_rejected", reason="instance stopped")
            self.request_stop()
            return
        except (SQLAlchemyError, OSError, PersistenceError) as exc:
            self._log.warning("worker.heartbeat_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            self._fence_if_lease_unconfirmed()
            return
        self.heartbeats += 1
        self._last_heartbeat_ok = monotonic()
        self._unfence()
        if self._status == "starting" and not self._stop.is_set():
            # M-1: одна невдала спроба на boot не має робити worker «живим, але німим» —
            # перехід повторюється, доки instance не стане ready (або доки процес не зупинять).
            await self._set_status("ready")
        self._apply_pool(snapshot)
        self._apply_drain_barrier(drain_requested)
        for job_id in lost:
            self._abandon(job_id)

    # --- self-fencing ---------------------------------------------------------------------

    def _fence_if_lease_unconfirmed(self) -> None:
        """Скасувати роботу, якщо база не підтверджує lease довше за безпечне вікно.

        Без цього недоступність PostgreSQL довша за `lease_seconds` означає **подвійне
        виконання**: lease спливає, `recover_expired_leases` віддає job іншому instance, а цей
        продовжує її робити і дізнається про втрату лише в момент звіту. Для `NoopHandler` це
        нешкідливо, для доменних handler-ів (Mongo-запис WP-01B, зовнішній запит WP-02) — ні.
        Тому, щойно з моменту останнього підтвердженого heartbeat минуло
        `fence_after_seconds` (типово половина lease TTL), instance сам себе відгороджує:
        скасовує активні tasks, не звітує за ними `complete` і не бере нових, поки база не
        підтвердить heartbeat знову (§7.6, §9.3).
        """
        if self._fenced:
            return
        elapsed = monotonic() - self._last_heartbeat_ok
        if elapsed < self.config.fence_after:
            return
        self._fenced = True
        self.fences += 1
        self._log.error(
            "worker.fenced",
            reason="lease not confirmed by database",
            seconds_since_heartbeat=round(elapsed, 3),
            fence_after_seconds=self.config.fence_after,
            lease_seconds=self.config.lease_seconds,
            cancelled_tasks=len(self._active),
        )
        for job_id in list(self._active):
            self._abandon(job_id, phase="fence")
        self._wakeup.set()

    def _unfence(self) -> None:
        if not self._fenced:
            return
        self._fenced = False
        self._log.info("worker.unfenced", fences=self.fences)
        self._wakeup.set()

    def _apply_pool(self, snapshot: PoolSnapshot) -> None:
        previous = self._pool
        self._pool = snapshot
        if snapshot.desired_concurrency > self.effective_concurrency:
            # M-3: гарячий hot-change не може вимагати більше слотів, ніж процес здатний
            # обслужити з'єднаннями — інакше checkout-и з pool-у впираються в `pool_timeout`,
            # heartbeat падає з `TimeoutError` і здорові tasks скасовуються «через базу».
            self._log.warning(
                "worker.concurrency_clamped",
                desired=snapshot.desired_concurrency,
                effective=self.effective_concurrency,
                max_concurrency=self.config.max_concurrency,
                detail=(
                    "підніміть COLLECTOR_WORKER_MAX_CONCURRENCY і перезапустіть репліку — "
                    "розмір pool з'єднань фіксується на старті процесу"
                ),
            )
        if snapshot.desired_concurrency != previous.desired_concurrency:
            self._log.info(
                "worker.concurrency_changed",
                previous=previous.desired_concurrency,
                desired=snapshot.desired_concurrency,
                pool_revision=snapshot.revision,
                active_tasks=len(self._active),
            )
            # Нові слоти відкриваються одразу; зайві закриються самі, коли активні tasks
            # завершаться (claim-loop бере не більше `desired - active`).
            self._wakeup.set()

    def _apply_drain_barrier(self, requested: bool) -> None:
        if requested == self._drain_barrier:
            return
        self._drain_barrier = requested
        self._log.info("worker.drain_barrier", active=requested)
        self._wakeup.set()

    def _abandon(self, job_id: UUID, *, phase: str = "heartbeat") -> None:
        """Lease job-и більше не наш: скасувати локальний task, не чіпаючи рядок у черзі.

        Рядок у `crawl_jobs` навмисно не змінюється: або його вже перехопив інший instance
        (`heartbeat`), або база недоступна (`fence`) — в обох випадках писати туди нічого і
        нічим. Скасована task не проходить через `_report`, тому тихого `complete` не буде.
        """
        entry = self._active.pop(job_id, None)
        if entry is None:
            return
        self.lost_leases += 1
        self._log.warning(
            "worker.lease_lost",
            job_id=str(job_id),
            phase=phase,
            fences=self.fences,
            lost_leases=self.lost_leases,
        )
        entry.handle.cancel()
        self._cancelled.append(entry.handle)

    # --- drain -------------------------------------------------------------------------------

    async def _drain(self) -> None:
        """SIGTERM-шлях: `draining`, дотягнути активні tasks, повернути залишки lease, `stopped`."""
        await self._set_status("draining")
        deadline = monotonic() + self.config.stop_grace_seconds
        handles = [entry.handle for entry in self._active.values()]
        if handles:
            timeout = max(deadline - monotonic(), 0.0)
            _, pending = await asyncio.wait(handles, timeout=timeout)
            if pending:
                self._log.warning("worker.drain_timeout", pending=len(pending))
                for handle in pending:
                    handle.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await self._release_leases()
        await self._await_cancelled_tasks()
        await self._set_status("stopped")
        self._log.info(
            "worker.stopped",
            heartbeats=self.heartbeats,
            lost_leases=self.lost_leases,
            fences=self.fences,
        )

    async def _await_cancelled_tasks(self) -> None:
        """Дочекатись скасованих (fencing/втрата lease) tasks — щоб handler закрив свої ресурси."""
        pending = [task for task in self._cancelled if not task.done()]
        self._cancelled.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _release_leases(self) -> None:
        """Повернути lease незавершених tasks у чергу через `queue.release` (claimable одразу).

        Плановий drain нікого не «провалив», тому це не `retry`: `release` (WP-01A PR2) не пише
        полів помилки, не створює dead letter, не карантинить job на останній спробі і
        компенсує інкремент `attempt`, який зробив claim (deps WP-01A→WP-01D §2, §6).
        """
        now = self._now()
        for job_id in list(self._active):
            try:
                async with self._transaction() as session:
                    await queue_repo.release(session, job_id, self.owner, now=now)
            except LeaseNotOwnedError:
                self._log.info("worker.lease_already_released", job_id=str(job_id))
            except (SQLAlchemyError, OSError, PersistenceError) as exc:
                # Job лишається `leased` і повернеться через `recover_expired_leases` (§15).
                self._log.error(
                    "worker.lease_release_failed",
                    job_id=str(job_id),
                    error=redact(f"{type(exc).__name__}: {exc}")[:300],
                )
            else:
                self._log.info("worker.lease_released", job_id=str(job_id))
            self._active.pop(job_id, None)

    async def _set_status(self, status: str) -> bool:
        """Записати статус instance; `False` — база не підтвердила перехід (викликач вирішує)."""
        try:
            async with self._transaction() as session:
                await pools_repo.set_instance_status(
                    session, self.instance_id, status, now=self._now()
                )
        except (SQLAlchemyError, OSError, PersistenceError) as exc:
            self._log.error(
                "worker.status_update_failed",
                status=status,
                error=redact(f"{type(exc).__name__}: {exc}"),
            )
            return False
        self._status = status
        self._log.info("worker.status", status=status)
        return True

    # --- утиліти -----------------------------------------------------------------------------

    async def _idle(self, timeout: float) -> None:
        """Пауза до найближчої події (звільнення слоту, зміна pool, зупинка) або таймауту."""
        with suppress(TimeoutError):
            await asyncio.wait_for(self._wakeup.wait(), timeout)
        self._wakeup.clear()

    def _now(self) -> datetime:
        return self._clock()


def build_runtime(
    role: WorkerRole,
    sessions: async_sessionmaker[AsyncSession],
    *,
    config: WorkerRuntimeConfig | None = None,
) -> WorkerRuntime:
    """Runtime ролі з env-конфігурацією і handler-ом із реєстру (`collector worker <role>`)."""
    return WorkerRuntime(config or WorkerRuntimeConfig.from_env(role), sessions)


__all__ = [
    "MAX_CLAIM_BACKOFF_SECONDS",
    "PoolSnapshot",
    "WorkerRuntime",
    "WorkerRuntimeError",
    "build_runtime",
]
