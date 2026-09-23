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
"""

from __future__ import annotations

import inspect
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from collector.workers.roles import WorkerRole

Disposition = Literal["complete", "retry", "quarantine"]
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


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Рішення handler-а: `complete` | `retry` | `quarantine` (+ код/повідомлення помилки)."""

    disposition: Disposition
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.disposition == "complete":
            if self.error_code is not None:
                msg = "успішний результат не має error_code"
                raise ValueError(msg)
            return
        if not self.error_code:
            msg = f"результат {self.disposition!r} потребує error_code"
            raise ValueError(msg)
        if len(self.error_code) > ERROR_CODE_MAX_LENGTH:
            msg = f"error_code довший за {ERROR_CODE_MAX_LENGTH} символів"
            raise ValueError(msg)

    @classmethod
    def success(cls) -> TaskResult:
        return cls(disposition="complete")

    @classmethod
    def retryable(cls, error_code: str, error_message: str | None = None) -> TaskResult:
        return cls(disposition="retry", error_code=error_code, error_message=error_message)

    @classmethod
    def permanent(cls, error_code: str, error_message: str | None = None) -> TaskResult:
        return cls(disposition="quarantine", error_code=error_code, error_message=error_message)


class TaskHandler(ABC):
    """Доменна робота однієї ролі; runtime створює рівно один handler на процес."""

    @property
    @abstractmethod
    def job_types(self) -> tuple[str, ...]:
        """Типи jobs, які claim-ить runtime цієї ролі (непорожній кортеж)."""

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


HandlerFactory = Callable[[WorkerRole], TaskHandler]

HANDLER_FACTORIES: Final[dict[WorkerRole, HandlerFactory]] = {}
"""Реєстр доменних handler-ів: роль → фабрика. Доменні WP додають свої записи при імпорті
свого модуля; ролі без запису працюють на `NoopHandler`."""


def resolve_handler(role: WorkerRole) -> TaskHandler:
    """Handler ролі з реєстру або `NoopHandler`, якщо доменний ще не зареєстровано."""
    factory = HANDLER_FACTORIES.get(role)
    return factory(role) if factory is not None else NoopHandler(role)


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
    "HandlerFactory",
    "NoopHandler",
    "PermanentTaskError",
    "Task",
    "TaskHandler",
    "TaskResult",
    "check_handler_contract",
    "redact",
    "resolve_handler",
    "result_for_exception",
]
