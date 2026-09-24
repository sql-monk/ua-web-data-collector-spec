"""Runtime-передумови доменних handler-ів на `crawl_jobs` (WP-01D PR1c п.1–4; §10, §7.6).

PostgreSQL 18 і LOGIN-ролі (`collector_fetcher`), як у PR1b. Годинник runtime — керований
(`Clock`): затримки retry порівнюються точно, а «час минув» — це `clock.now = ...`, не `sleep`.

- `TaskResult.deferred` → job `pending`, `attempt` той самий, помилки не записані, dead letter
  немає навіть після п'яти defer поспіль при `max_attempts=4`;
- `retryable(not_before=)` — нижня межа наступної спроби; `retry_schedule` 5 с/30 с/2 хв/10 хв
  дає рівно ці затримки, 4-та невдача при `max_attempts=4` → карантин + dead letter;
- без `retry_schedule` — `BackoffPolicy` черги, як до PR1c (регресія);
- фабрика з реєстру отримує `HandlerContext` з тими самими `sessions` і id зареєстрованого instance.

API WP-01A PR3a (`queue.retry/release(not_before=)`) merged у PR #12 і перевіряється напряму.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.persistence.postgres.models import CrawlJob, DeadLetter, WorkerInstance
from collector.persistence.postgres.repositories import queue as queue_repo
from collector.workers import registry
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import (
    HANDLER_FACTORIES,
    HandlerContext,
    RetrySchedule,
    Task,
    TaskHandler,
    TaskResult,
)
from collector.workers.roles import WorkerRole
from collector.workers.runtime import DEFAULT_BACKOFF, WorkerRuntime

from .conftest import PAST, PATIENT_TIMEOUT, T0, WaitFor

pytestmark = pytest.mark.integration

MakePool = Callable[..., Awaitable[int]]
MakeConfig = Callable[..., WorkerRuntimeConfig]


@pytest.fixture
def wait_for(patient_wait_for: WaitFor) -> WaitFor:
    """Бюджет `PATIENT_TIMEOUT` (conftest): очікування включає boot runtime під LOGIN-роллю."""
    return patient_wait_for


SPEC_10 = RetrySchedule(
    (timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
)


class Clock:
    """Керований годинник runtime: тест сам пересуває `now`."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class ScriptedHandler(TaskHandler):
    """Handler, що повертає результат за скриптом (функція від task)."""

    def __init__(
        self,
        decide: Callable[[Task], TaskResult],
        *,
        schedule: RetrySchedule | None = None,
    ) -> None:
        self._decide = decide
        self._schedule = schedule
        self.seen: list[Task] = []

    @property
    def job_types(self) -> tuple[str, ...]:
        return ("fetch",)

    @property
    def retry_schedule(self) -> RetrySchedule | None:
        return self._schedule

    async def handle(self, task: Task) -> TaskResult:
        self.seen.append(task)
        return self._decide(task)


async def enqueue(
    sessions: async_sessionmaker[AsyncSession], *, max_attempts: int, key: str = "plumbing:0"
) -> UUID:
    async with sessions() as session, session.begin():
        job = await queue_repo.enqueue(
            session,
            queue_repo.NewJob(
                job_type="fetch", idempotency_key=key, args={"n": 0}, max_attempts=max_attempts
            ),
            now=PAST,
        )
        return job.job_id


async def read_job(sessions: async_sessionmaker[AsyncSession], job_id: UUID) -> CrawlJob:
    async with sessions() as session:
        job = await session.get(CrawlJob, job_id)
    assert job is not None
    return job


async def dead_letters(sessions: async_sessionmaker[AsyncSession], job_id: UUID) -> list[str]:
    async with sessions() as session:
        rows = await session.scalars(select(DeadLetter.reason).where(DeadLetter.job_id == job_id))
        return list(rows)


class Harness:
    """Один runtime ролі `fetch` під `collector_fetcher` з керованим годинником."""

    def __init__(
        self,
        runtime: WorkerRuntime,
        running: list[asyncio.Task[None]],
    ) -> None:
        self.runtime = runtime
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(runtime.run(stop=self.stop, install_signals=False))
        running.append(self.task)

    async def close(self) -> None:
        self.stop.set()
        await asyncio.wait_for(self.task, timeout=PATIENT_TIMEOUT)


@pytest.fixture
def harness(
    worker_config: MakeConfig,
    runtime_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
) -> Callable[[TaskHandler, Clock], Harness]:
    def make(handler: TaskHandler, clock: Clock) -> Harness:
        runtime = WorkerRuntime(worker_config(), runtime_sessions, handler, clock=clock)
        return Harness(runtime, running)

    return make


# --- defer ----------------------------------------------------------------------------------


async def test_deferred_job_keeps_its_attempt_and_writes_no_error(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    harness: Callable[[TaskHandler, Clock], Harness],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)
    job_id = await enqueue(pg_sessions, max_attempts=4)
    until = T0 + timedelta(minutes=10)
    handler = ScriptedHandler(lambda _task: TaskResult.deferred(until, "rate_limited"))
    run = harness(handler, Clock(T0))

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт про defer")
    job = await read_job(pg_sessions, job_id)
    assert run.runtime.report_failures == 0
    assert (job.status, job.attempt, job.not_before) == ("pending", 0, until)
    assert (job.last_error_code, job.last_error_message) == (None, None)
    assert job.lease_owner is None
    assert await dead_letters(pg_sessions, job_id) == []
    await run.close()


async def test_five_defers_in_a_row_never_dead_letter_a_job_with_four_attempts(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    harness: Callable[[TaskHandler, Clock], Harness],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)
    job_id = await enqueue(pg_sessions, max_attempts=4)
    clock = Clock(T0)
    handler = ScriptedHandler(
        lambda _task: TaskResult.deferred(clock.now + timedelta(seconds=1), "permit_denied")
    )
    run = harness(handler, clock)

    for done in range(1, 6):
        await wait_for(lambda done=done: run.runtime.reports >= done, what=f"defer №{done}")
        # Пересуваємо час лише щоб зробити доступною наступну з п'яти перевірених видач.
        # Після п'ятої не відкриваємо шосту: runtime працює паралельно й інакше може встигнути
        # claim-нути її до точного assert нижче, хоча invariant "attempt не згоряє" виконано.
        if done < 5:
            clock.now += timedelta(seconds=1)
    job = await read_job(pg_sessions, job_id)
    assert run.runtime.report_failures == 0
    assert [task.attempt for task in handler.seen] == [1, 1, 1, 1, 1], "спроба не згоряє"
    assert (job.status, job.attempt) == ("pending", 0)
    assert await dead_letters(pg_sessions, job_id) == []
    await run.close()


# --- retry: нижня межа і табличний розклад -------------------------------------------------


async def test_retry_after_lower_bound_beats_a_short_schedule(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    harness: Callable[[TaskHandler, Clock], Harness],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)
    job_id = await enqueue(pg_sessions, max_attempts=4)
    retry_after = T0 + timedelta(hours=2)
    handler = ScriptedHandler(
        lambda _task: TaskResult.retryable("http_429", not_before=retry_after),
        schedule=RetrySchedule((timedelta(seconds=5),)),
    )
    run = harness(handler, Clock(T0))

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт про retry")
    job = await read_job(pg_sessions, job_id)
    assert run.runtime.report_failures == 0
    assert job.status == "retry"
    assert job.not_before >= retry_after
    assert (job.attempt, job.last_error_code) == (1, "http_429")
    await run.close()


async def test_retry_schedule_gives_exactly_the_spec_10_delays(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    harness: Callable[[TaskHandler, Clock], Harness],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)
    job_id = await enqueue(pg_sessions, max_attempts=5)
    clock = Clock(T0)
    handler = ScriptedHandler(lambda _task: TaskResult.retryable("http_503"), schedule=SPEC_10)
    run = harness(handler, clock)

    for attempt, delay in enumerate(SPEC_10.delays, start=1):
        await wait_for(lambda a=attempt: run.runtime.reports >= a, what=f"retry №{attempt}")
        job = await read_job(pg_sessions, job_id)
        assert run.runtime.report_failures == 0
        assert (job.status, job.attempt) == ("retry", attempt)
        assert job.not_before == clock.now + delay, f"спроба {attempt}: затримка {delay}"
        clock.now = job.not_before
    await run.close()


async def test_fourth_failure_with_four_attempts_is_quarantined_with_a_dead_letter(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    harness: Callable[[TaskHandler, Clock], Harness],
    wait_for: WaitFor,
) -> None:
    await make_pool(concurrency=1)
    job_id = await enqueue(pg_sessions, max_attempts=4)
    clock = Clock(T0)
    handler = ScriptedHandler(lambda _task: TaskResult.retryable("http_503"), schedule=SPEC_10)
    run = harness(handler, clock)

    for attempt in range(1, 5):
        await wait_for(lambda a=attempt: run.runtime.reports >= a, what=f"невдача №{attempt}")
        job = await read_job(pg_sessions, job_id)
        clock.now = max(clock.now, job.not_before)
    job = await read_job(pg_sessions, job_id)
    assert run.runtime.report_failures == 0
    assert (job.status, job.attempt, job.last_error_code) == ("quarantined", 4, "http_503")
    assert await dead_letters(pg_sessions, job_id) == ["max_attempts"]
    await run.close()


async def test_handler_without_schedule_keeps_the_queue_backoff(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    harness: Callable[[TaskHandler, Clock], Harness],
    wait_for: WaitFor,
) -> None:
    """Регресія: без `retry_schedule` і `not_before` — `BackoffPolicy()` черги, як до PR1c."""
    await make_pool(concurrency=1)
    job_id = await enqueue(pg_sessions, max_attempts=4)
    handler = ScriptedHandler(lambda _task: TaskResult.retryable("handler_error"))
    run = harness(handler, Clock(T0))

    await wait_for(lambda: run.runtime.reports >= 1, what="звіт про retry")
    job = await read_job(pg_sessions, job_id)
    base = DEFAULT_BACKOFF.base
    assert run.runtime.report_failures == 0
    assert (job.status, job.attempt, job.last_error_code) == ("retry", 1, "handler_error")
    assert T0 + base <= job.not_before <= T0 + base * (1 + DEFAULT_BACKOFF.jitter_ratio)
    await run.close()


# --- реєстр і HandlerContext ----------------------------------------------------------------


@pytest.fixture
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Без мапи модулів: справжній `collector.fetch.handler` (WP-02), якщо він уже є, не має
    # перезаписати фабрику тесту.
    monkeypatch.setattr(registry, "ROLE_HANDLER_MODULES", {})
    saved = dict(HANDLER_FACTORIES)
    HANDLER_FACTORIES.clear()
    try:
        yield
    finally:
        HANDLER_FACTORIES.clear()
        HANDLER_FACTORIES.update(saved)


async def test_factory_gets_the_runtime_sessions_and_the_registered_instance_id(
    pg_sessions: async_sessionmaker[AsyncSession],
    make_pool: MakePool,
    worker_config: MakeConfig,
    runtime_sessions: async_sessionmaker[AsyncSession],
    running: list[asyncio.Task[None]],
    wait_for: WaitFor,
    clean_registry: None,
) -> None:
    await make_pool(concurrency=1)
    job_id = await enqueue(pg_sessions, max_attempts=4)
    contexts: list[HandlerContext] = []
    handler = ScriptedHandler(lambda _task: TaskResult.success())

    def factory(context: HandlerContext) -> TaskHandler:
        contexts.append(context)
        return handler

    HANDLER_FACTORIES[WorkerRole.FETCH] = factory
    run = Harness(WorkerRuntime(worker_config(), runtime_sessions), running)

    await wait_for(lambda: run.runtime.reports >= 1, what="handler із фабрики виконав job")
    (context,) = contexts
    assert context.sessions is runtime_sessions, "той самий async_sessionmaker, не другий pool"
    assert context.worker_instance_id == run.runtime.instance_id
    async with pg_sessions() as session:
        instance = await session.get(WorkerInstance, context.worker_instance_id)
    assert instance is not None and instance.role == WorkerRole.FETCH.value
    assert (await read_job(pg_sessions, job_id)).status == "succeeded"
    await run.close()
