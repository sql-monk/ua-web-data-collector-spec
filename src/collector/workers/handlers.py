"""Інтерфейс доменної роботи worker-а: `Task`, `TaskResult`, `TaskHandler`, `NoopHandler`.

Runtime (`collector.workers.runtime`) відповідає за claim/lease/heartbeat/drain і не знає нічого
про домен. Доменні WP (fetch — WP-02, discovery — WP-03, translation — WP-04, projector —
WP-01B) реалізують `TaskHandler` і реєструють його у `HANDLER_FACTORIES` для своєї ролі.

Контракт handler-а:

- `job_types` — типи jobs, які claim-ить runtime для цієї ролі; має збігатися з `job_type`
  тих jobs, які ставить у чергу планувальник домену;
- `check_ready()` — readiness-перевірка залежностей ролі (§7.5: instance стає `ready` лише
  після неї); за замовчуванням нічого не перевіряє;
- `handle(task)` — робить роботу і повертає `TaskResult`. **Не** викликає `complete`/`retry`
  сам: статус job-и у черзі пише runtime у своїй транзакції. Handler не зберігає стан на
  локальному диску (§15) і має бути придатним до скасування (`asyncio.CancelledError` під час
  drain-timeout).

**Handler не має права блокувати event loop.** `handle` виконується як `asyncio.Task` у тому
самому loop-і, що claim-loop, heartbeat і сторож lease, а `cancel()` — кооперативний. Тому
синхронна CPU-bound робота (парсинг HTML, regex по великому body, розпакування) **одночасно**
зупиняє heartbeat (lease спливає), self-fencing (нікому рахувати час) і drain (task не
скасовується у межах `stop_grace_period`). Правило: будь-яку роботу, що не віддає керування
довше за десятки мілісекунд, виносити в `await asyncio.to_thread(...)` або в executor. Runtime
перевіряє контракт на boot (`check_handler_contract`: `handle` має бути `async def`, `job_types`
— непорожній) і логує `worker.event_loop_stalled`, якщо сторож прокидається пізно (M-2
код-рев'ю).

Тексти помилок, які повертає handler, потрапляють у `crawl_jobs.last_error_message`,
`dead_letters` і логи — `redact()` прибирає з них credentials у URL і значення
token/password/api_key параметрів (§13), але handler усе одно не має класти туди секрети
свідомо.

Винятки handler-а runtime трактує як retryable (`result_for_exception`), окрім
`PermanentTaskError` — він означає карантин без подальших спроб.

PR1c (передумова хвилі 1): `TaskResult.deferred` (відкласти без спалювання спроби),
`TaskResult.retryable(..., not_before=)` (нижня межа наступної спроби, наприклад `Retry-After`),
`TaskHandler.retry_schedule` (таблична затримка §10 замість дефолтного `BackoffPolicy`),
`TaskResult.success(output=)` (результат для report-транзакції, напр. receipt projector-а) і
`HandlerContext`, який фабрика ролі отримує від runtime. Реєстр із lazy import доменних модулів
— `collector.workers.registry`; черги (`crawl_jobs`, `projection_tasks`) —
`collector.workers.backends`.
"""

from __future__ import annotations

import inspect
import random
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal
from uuid import UUID

from collector.workers.roles import WorkerRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from collector.workers.backends import QueueBackend

Disposition = Literal["complete", "retry", "quarantine", "defer"]
ERROR_CODE_MAX_LENGTH = 64


class PermanentTaskError(Exception):
    """Помилка, після якої повторювати task не можна: job іде в карантин + dead letter."""

    def __init__(self, message: str, *, error_code: str = "permanent_error") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class Task:
    """Знімок job-и для handler-а: без ORM-об'єктів і без доступу до session/транзакції."""

    job_id: UUID
    job_type: str
    args: Mapping[str, object]
    attempt: int
    max_attempts: int
    priority: int
    not_before: datetime
    run_id: UUID | None
    source_id: UUID | None


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{name} має бути timezone-aware (UTC), отримано naive {value!r}"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Рішення handler-а: `complete` | `retry` | `quarantine` | `defer`.

    - `complete` — успіх; `output` — необов'язковий результат, який runtime передає в
      report-транзакцію backend-а (для `projection_tasks` — receipt для ack);
    - `retry` — retryable-помилка; `not_before` — **нижня межа** наступної спроби (напр.
      `Retry-After`): runtime бере `max(now + затримка з розкладу, not_before)`;
    - `quarantine` — permanent failure (карантин + dead letter);
    - `defer` — «ще не час» (limiter відмовив, source paused, бюджет вичерпано): job
      повертається в чергу з `not_before = until` **без** спалювання спроби, без полів помилки
      і без dead letter. `error_code` обов'язковий — він іде в лог `worker.task_deferred`.
    """

    disposition: Disposition
    error_code: str | None = None
    error_message: str | None = None
    not_before: datetime | None = None
    output: object | None = None

    def __post_init__(self) -> None:
        if self.output is not None and self.disposition != "complete":
            msg = f"output дозволений лише для complete, не для {self.disposition!r}"
            raise ValueError(msg)
        if self.not_before is not None:
            if self.disposition not in {"retry", "defer"}:
                msg = f"not_before дозволений лише для retry/defer, не для {self.disposition!r}"
                raise ValueError(msg)
            _require_aware(self.not_before, "not_before")
        if self.disposition == "complete":
            if self.error_code is not None or self.error_message is not None:
                msg = "успішний результат не має error_code/error_message"
                raise ValueError(msg)
            return
        if not self.error_code:
            msg = f"результат {self.disposition!r} потребує error_code"
            raise ValueError(msg)
        if len(self.error_code) > ERROR_CODE_MAX_LENGTH:
            msg = f"error_code довший за {ERROR_CODE_MAX_LENGTH} символів"
            raise ValueError(msg)
        if self.disposition == "defer":
            if self.not_before is None:
                msg = "defer потребує until (not_before)"
                raise ValueError(msg)
            if self.error_message is not None:
                # Defer нікого не провалив: runtime не пише полів помилки, тож текст мовчки
                # губився б — краще відмовити одразу.
                msg = "defer не пише полів помилки — error_message не приймається"
                raise ValueError(msg)

    @classmethod
    def success(cls, output: object | None = None) -> TaskResult:
        return cls(disposition="complete", output=output)

    @classmethod
    def retryable(
        cls,
        error_code: str,
        error_message: str | None = None,
        *,
        not_before: datetime | None = None,
    ) -> TaskResult:
        return cls(
            disposition="retry",
            error_code=error_code,
            error_message=error_message,
            not_before=not_before,
        )

    @classmethod
    def permanent(cls, error_code: str, error_message: str | None = None) -> TaskResult:
        return cls(disposition="quarantine", error_code=error_code, error_message=error_message)

    @classmethod
    def deferred(cls, until: datetime, error_code: str) -> TaskResult:
        return cls(disposition="defer", error_code=error_code, not_before=until)


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    """Таблична затримка retry (§10: 5 с / 30 с / 2 хв / 10 хв), яку задає handler.

    Затримка для спроби `n` (1-based, `Task.attempt` після claim) — `delays[min(n, len) - 1]`
    плюс рівномірний jitter у `[0, delay * jitter_ratio]` — jitter обмежений зверху цією межею.
    `max_attempts` — властивість job-и (ставить планувальник домену при enqueue), не розкладу.
    """

    delays: tuple[timedelta, ...]
    jitter_ratio: float = 0.0

    def __post_init__(self) -> None:
        if not self.delays:
            msg = "RetrySchedule.delays не може бути порожнім"
            raise ValueError(msg)
        if any(delay <= timedelta(0) for delay in self.delays):
            msg = "кожна затримка RetrySchedule має бути > 0"
            raise ValueError(msg)
        if not 0.0 <= self.jitter_ratio <= 1.0:
            msg = f"jitter_ratio має бути у [0, 1], отримано {self.jitter_ratio}"
            raise ValueError(msg)

    def delay(self, attempt: int, rng: random.Random) -> timedelta:
        base = self.delays[min(max(attempt, 1), len(self.delays)) - 1]
        if self.jitter_ratio <= 0:
            return base
        return base + timedelta(seconds=rng.uniform(0.0, base.total_seconds() * self.jitter_ratio))


class TaskHandler(ABC):
    """Доменна робота однієї ролі; runtime створює рівно один handler на процес."""

    @property
    @abstractmethod
    def job_types(self) -> tuple[str, ...]:
        """Типи jobs, які claim-ить runtime цієї ролі (непорожній кортеж)."""

    @property
    def retry_schedule(self) -> RetrySchedule | None:
        """Таблична затримка retry; `None` — дефолтний `BackoffPolicy` черги (як до PR1c)."""
        return None

    async def check_ready(self) -> None:
        """Readiness-перевірка залежностей ролі; виняток = instance не стає `ready`."""
        return None

    @abstractmethod
    async def handle(self, task: Task) -> TaskResult:
        """Виконати task. Винятки трактуються за `result_for_exception`."""


class NoopHandler(TaskHandler):
    """Handler-заглушка: claim-ить `job_type == role` і одразу звітує успіх.

    Потрібен, щоб runtime-каркас (claim/lease/heartbeat/drain) можна було запускати й тестувати
    до появи доменних handler-ів, і щоб контейнер ролі без власного handler-а не падав, а
    працював як порожній pool (видно у `worker_instances`).
    """

    def __init__(self, role: WorkerRole) -> None:
        self._role = role

    @property
    def job_types(self) -> tuple[str, ...]:
        return (self._role.value,)

    async def handle(self, task: Task) -> TaskResult:
        return TaskResult.success()


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """Що runtime дає фабриці handler-а ролі (PR1c п.4).

    - `sessions` — **той самий** `async_sessionmaker`, що й у runtime (одна LOGIN-роль §13,
      один pool з'єднань): handler не відкриває другого pool-у під тим самим DSN;
    - `worker_instance_id` — id зареєстрованого instance; `owner` (рядок) — власник upload
      claims/origin permits, той самий, що `lease_owner` jobs цього instance;
    - `clock` — годинник runtime (UTC); `env` — env процесу (конфігурація домену).
    """

    role: WorkerRole
    sessions: async_sessionmaker[AsyncSession]
    worker_instance_id: UUID
    clock: Callable[[], datetime]
    env: Mapping[str, str]

    @property
    def owner(self) -> str:
        return str(self.worker_instance_id)


@dataclass(frozen=True, slots=True)
class HandlerBinding:
    """Прив'язка handler-а до черги; роль може мати кілька (PR1c п.5).

    `projector`: `ProjectionTasksBackend` + `ProjectorHandler` і `CrawlJobsBackend` + handler-и
    `projection.reconcile`/`projection.compact`. Слоти `desired_concurrency` спільні для всіх
    прив'язок ролі; claim по прив'язках — round-robin.
    """

    backend: QueueBackend
    handler: TaskHandler


HandlerFactory = Callable[[HandlerContext], TaskHandler | Sequence[HandlerBinding]]
"""Фабрика ролі: один `TaskHandler` (черга `crawl_jobs`) або кілька `HandlerBinding`."""

HANDLER_FACTORIES: Final[dict[WorkerRole, HandlerFactory]] = {}
"""Реєстр доменних handler-ів: роль → фабрика. Доменний модуль ролі додає свій запис при
імпорті, а імпортує модуль runtime — ліниво, через `collector.workers.registry`. Ролі без
модуля і без запису працюють на `NoopHandler`."""


_CREDENTIALS_IN_URL = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]*://)[^/\s:@]+:[^/\s@]*@")
_SECRET_QUERY_PARAM = re.compile(
    r"(?i)(token|api[_-]?key|apikey|password|passwd|secret|signature|sig)=[^\s&#\"']+"
)
REDACTED = "[redacted]"


def redact(message: str) -> str:
    """Прибрати з тексту credentials у URL і значення секретних query-параметрів (§13).

    Runtime пише тексти винятків у `crawl_jobs.last_error_message`, `dead_letters` і логи —
    дешевше зробити редакцію тут, ніж покладатися на дисципліну пʼяти доменних WP (L-7
    код-рев'ю). Це страховка, а не дозвіл класти секрети в повідомлення.
    """
    redacted = _CREDENTIALS_IN_URL.sub(rf"\g<scheme>{REDACTED}@", message)
    return _SECRET_QUERY_PARAM.sub(lambda m: f"{m.group(1)}={REDACTED}", redacted)


def check_handler_contract(handler: TaskHandler) -> None:
    """Перевірити контракт handler-а на boot: `async def handle` і непорожні `job_types`.

    Синхронний `handle` заблокував би event loop разом із heartbeat, fencing і drain (M-2),
    а порожній `job_types` дав би мовчазний простій: `claim` повертав би `[]` вічно (L-7).
    Обидві помилки коштують дешево на старті й дуже дорого — у проді.
    """
    if not inspect.iscoroutinefunction(handler.handle):
        msg = (
            f"{type(handler).__name__}.handle має бути `async def`: синхронний handler блокує "
            "event loop разом із heartbeat, self-fencing і drain; CPU-bound роботу виносьте в "
            "asyncio.to_thread"
        )
        raise TypeError(msg)
    if not handler.job_types:
        msg = f"{type(handler).__name__}.job_types порожній — worker не claim-ив би нічого"
        raise ValueError(msg)


def result_for_exception(exc: BaseException) -> TaskResult:
    """Виняток handler-а → `TaskResult`: `PermanentTaskError` → карантин, решта → retry.

    Текст винятку потрапляє у `crawl_jobs.last_error_message`/`dead_letters` (обрізається
    репозиторієм), тому handler не має класти в нього секрети чи контакти (§13).
    """
    message = redact(f"{type(exc).__name__}: {exc}")
    if isinstance(exc, PermanentTaskError):
        return TaskResult.permanent(exc.error_code, message)
    return TaskResult.retryable("handler_error", message)


__all__ = [
    "HANDLER_FACTORIES",
    "REDACTED",
    "HandlerBinding",
    "HandlerContext",
    "HandlerFactory",
    "NoopHandler",
    "PermanentTaskError",
    "RetrySchedule",
    "Task",
    "TaskHandler",
    "TaskResult",
    "check_handler_contract",
    "redact",
    "result_for_exception",
]
