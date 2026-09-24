"""Звіт runtime за `TaskResult` і round-robin прив'язок (WP-01D PR1c п.1, п.2, п.5) — без БД.

Черга підміняється `RecordingBackend` (реалізація `QueueBackend`, що записує виклики), сесії —
`FakeSessions`: так перевіряється саме рішення runtime-у — **який** метод черги і з якими
аргументами він викликає. Що ці методи роблять у PostgreSQL (attempt, dead letter, ack в одній
транзакції), доводять integration-тести `tests/integration/scaling/test_handler_plumbing.py`.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from structlog.testing import capture_logs

from collector.contracts import new_entity_id
from collector.workers.backends import CRAWL_JOBS, InvalidHandlerOutputError
from collector.workers.config import WorkerRuntimeConfig
from collector.workers.handlers import (
    HandlerBinding,
    RetrySchedule,
    Task,
    TaskHandler,
    TaskResult,
)
from collector.workers.roles import WorkerRole
from collector.workers.runtime import DEFAULT_BACKOFF, WorkerRuntime

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SPEC_10 = RetrySchedule(
    (timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
)


class FakeSession:
    async def execute(self, *_args: object, **_kwargs: object) -> None:
        return None

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield


@asynccontextmanager
async def fake_sessions() -> AsyncIterator[FakeSession]:
    yield FakeSession()


@dataclass
class RecordingBackend:
    """`QueueBackend`, що записує виклики; `claim` видає нескінченний потік tasks."""

    label: str = "fake"
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_with: Exception | None = None
    reject_output: bool = False

    @property
    def name(self) -> str:
        return self.label

    def check_output(self, output: object) -> None:
        if self.reject_output and output is not None:
            msg = "fake queue takes no output"
            raise InvalidHandlerOutputError(msg)

    def _record(self, method: str, **kwargs: Any) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append((method, kwargs))

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    async def claim(
        self,
        session: object,
        job_types: Sequence[str],
        owner: str,
        lease_seconds: int,
        *,
        limit: int,
        now: datetime,
    ) -> list[Task]:
        self.calls.append(("claim", {"limit": limit}))
        return [make_task(job_types[0]) for _ in range(limit)]

    async def heartbeat(
        self, session: object, job_id: UUID, owner: str, lease_seconds: int, *, now: datetime
    ) -> None:
        return None

    async def complete(
        self, session: object, task: Task, owner: str, output: object, *, now: datetime
    ) -> None:
        self._record("complete", job_id=task.job_id, output=output)

    async def retry(
        self,
        session: object,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        not_before: datetime | None,
        now: datetime,
    ) -> str:
        self._record("retry", error_code=error_code, not_before=not_before)
        return "retry"

    async def defer(
        self, session: object, job_id: UUID, owner: str, *, until: datetime, now: datetime
    ) -> None:
        self._record("defer", until=until)

    async def quarantine(
        self,
        session: object,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        now: datetime,
    ) -> None:
        self._record("quarantine", error_code=error_code, error_message=error_message)

    async def release(self, session: object, job_id: UUID, owner: str, *, now: datetime) -> None:
        self._record("release", job_id=job_id)

    async def recover_expired(self, session: object, *, limit: int, now: datetime) -> list[UUID]:
        return []


def make_task(job_type: str = "fetch.http", *, attempt: int = 1) -> Task:
    return Task(
        job_id=new_entity_id(),
        job_type=job_type,
        args={},
        attempt=attempt,
        max_attempts=4,
        priority=100,
        not_before=NOW,
        run_id=None,
        source_id=None,
    )


class Handler(TaskHandler):
    def __init__(
        self, job_type: str = "fetch.http", *, schedule: RetrySchedule | None = None
    ) -> None:
        self._job_type = job_type
        self._schedule = schedule
        self.handled: list[Task] = []

    @property
    def job_types(self) -> tuple[str, ...]:
        return (self._job_type,)

    @property
    def retry_schedule(self) -> RetrySchedule | None:
        return self._schedule

    async def handle(self, task: Task) -> TaskResult:
        self.handled.append(task)
        return TaskResult.success()


def runtime_for(*bindings: HandlerBinding, max_defer_seconds: float = 24 * 3600) -> WorkerRuntime:
    return WorkerRuntime(
        WorkerRuntimeConfig(role=WorkerRole.FETCH, max_defer_seconds=max_defer_seconds),
        cast("Any", fake_sessions),
        list(bindings),
        clock=lambda: NOW,
        rng=random.Random(0),  # noqa: S311 — детермінізм тесту, не криптографія
    )


async def report(
    result: TaskResult,
    *,
    schedule: RetrySchedule | None = None,
    attempt: int = 1,
    backend: RecordingBackend | None = None,
    max_defer_seconds: float = 24 * 3600,
) -> RecordingBackend:
    queue = backend or RecordingBackend()
    binding = HandlerBinding(cast("Any", queue), Handler(schedule=schedule))
    runtime = runtime_for(binding, max_defer_seconds=max_defer_seconds)
    await runtime._report(make_task(attempt=attempt), binding, result)  # noqa: SLF001
    assert runtime.reports == 1
    return queue


# --- defer ----------------------------------------------------------------------------------


async def test_defer_goes_through_release_not_retry() -> None:
    """Мутація «defer через queue.retry» робить цей тест червоним: retry спалив би спробу."""
    until = NOW + timedelta(minutes=10)
    queue = await report(TaskResult.deferred(until, "rate_limited"))
    assert queue.calls == [("defer", {"until": until})]


async def test_defer_into_the_past_becomes_now() -> None:
    queue = await report(TaskResult.deferred(NOW - timedelta(hours=1), "rate_limited"))
    assert queue.calls == [("defer", {"until": NOW})]


async def test_defer_beyond_the_ceiling_is_clamped_with_a_warning() -> None:
    with capture_logs() as logs:
        queue = await report(
            TaskResult.deferred(NOW + timedelta(days=30), "budget_exhausted"),
            max_defer_seconds=3600,
        )
    assert queue.calls == [("defer", {"until": NOW + timedelta(hours=1)})]
    clamped = [entry for entry in logs if entry["event"] == "worker.not_before_clamped"]
    assert len(clamped) == 1 and clamped[0]["log_level"] == "warning"
    deferred = [entry for entry in logs if entry["event"] == "worker.task_deferred"]
    assert deferred[0]["error_code"] == "budget_exhausted"
    assert "args" not in deferred[0], "лог без тексту задачі"


# --- retry ----------------------------------------------------------------------------------


async def test_retry_without_schedule_or_lower_bound_keeps_the_queue_backoff() -> None:
    """Регресія: handler без `retry_schedule` — черга рахує `BackoffPolicy` сама, як до PR1c."""
    queue = await report(TaskResult.retryable("handler_error"))
    assert queue.calls == [("retry", {"error_code": "handler_error", "not_before": None})]


@pytest.mark.parametrize("attempt", [1, 2, 3, 4])
async def test_retry_schedule_gives_exactly_the_table_delay(attempt: int) -> None:
    queue = await report(TaskResult.retryable("http_503"), schedule=SPEC_10, attempt=attempt)
    ((_, kwargs),) = queue.calls
    assert kwargs["not_before"] == NOW + SPEC_10.delays[attempt - 1]


async def test_retry_after_lower_bound_wins_over_a_shorter_schedule() -> None:
    later = NOW + timedelta(hours=2)
    queue = await report(TaskResult.retryable("http_429", not_before=later), schedule=SPEC_10)
    assert queue.calls[0][1]["not_before"] == later


async def test_schedule_wins_over_an_earlier_lower_bound() -> None:
    queue = await report(
        TaskResult.retryable("http_429", not_before=NOW + timedelta(seconds=1)),
        schedule=SPEC_10,
    )
    assert queue.calls[0][1]["not_before"] == NOW + timedelta(seconds=5)


async def test_lower_bound_without_schedule_uses_the_default_backoff_as_floor() -> None:
    queue = await report(TaskResult.retryable("http_429", not_before=NOW + timedelta(seconds=1)))
    not_before = queue.calls[0][1]["not_before"]
    base = DEFAULT_BACKOFF.base
    assert NOW + base <= not_before <= NOW + base * (1 + DEFAULT_BACKOFF.jitter_ratio)


async def test_retry_after_beyond_the_ceiling_is_clamped() -> None:
    queue = await report(
        TaskResult.retryable("http_429", not_before=NOW + timedelta(days=7)),
        max_defer_seconds=3600,
    )
    assert queue.calls[0][1]["not_before"] == NOW + timedelta(hours=1)


# --- complete / output / report failures ----------------------------------------------------


async def test_output_is_passed_to_the_queue_in_the_report() -> None:
    receipt = object()
    queue = await report(TaskResult.success(output=receipt))
    assert queue.calls[0][0] == "complete"
    assert queue.calls[0][1]["output"] is receipt


async def test_output_the_queue_rejects_quarantines_the_task() -> None:
    queue = await report(
        TaskResult.success(output="not a receipt"), backend=RecordingBackend(reject_output=True)
    )
    assert queue.methods() == ["quarantine"]
    assert queue.calls[0][1]["error_code"] == "invalid_handler_output"


def test_crawl_jobs_backend_rejects_any_output() -> None:
    CRAWL_JOBS.check_output(None)
    with pytest.raises(InvalidHandlerOutputError):
        CRAWL_JOBS.check_output(object())


async def test_unexpected_report_error_is_logged_not_lost() -> None:
    queue = RecordingBackend(fail_with=TypeError("unexpected keyword argument 'not_before'"))
    binding = HandlerBinding(cast("Any", queue), Handler())
    runtime = runtime_for(binding)
    with capture_logs() as logs:
        await runtime._report(make_task(), binding, TaskResult.success())  # noqa: SLF001
    assert (runtime.reports, runtime.report_failures) == (1, 1)
    assert [entry["event"] for entry in logs if entry["log_level"] == "error"] == [
        "worker.report_failed"
    ]


# --- кілька прив'язок: спільні слоти, round-robin ---------------------------------------------


async def test_two_bindings_share_one_slot_and_neither_starves() -> None:
    """`desired_concurrency=1`, обидві черги завжди непорожні → черги чергуються."""
    projection = RecordingBackend(label="projection_tasks")
    reconcile = RecordingBackend(label="crawl_jobs")
    runtime = runtime_for(
        HandlerBinding(cast("Any", projection), Handler("catalog_items")),
        HandlerBinding(cast("Any", reconcile), Handler("projection.reconcile")),
    )

    async def allowed(_session: object) -> bool:
        return True

    runtime._claim_allowed = allowed  # type: ignore[method-assign]  # noqa: SLF001 — без БД
    order: list[str] = []
    for _ in range(10):
        assert await runtime._claim(1) == 1  # noqa: SLF001
        ((job_id, entry),) = runtime._active.items()  # noqa: SLF001
        order.append(entry.binding.backend.name)
        await entry.handle
        assert job_id not in runtime._active  # noqa: SLF001
    assert order == ["projection_tasks", "crawl_jobs"] * 5
    assert runtime.claims_by_backend == {"projection_tasks": 5, "crawl_jobs": 5}


async def test_empty_binding_does_not_waste_the_slot() -> None:
    class Empty(RecordingBackend):
        async def claim(self, *args: Any, **kwargs: Any) -> list[Task]:
            return []

    busy = RecordingBackend(label="busy")
    runtime = runtime_for(
        HandlerBinding(cast("Any", Empty(label="empty")), Handler("a")),
        HandlerBinding(cast("Any", busy), Handler("b")),
    )

    async def allowed(_session: object) -> bool:
        return True

    runtime._claim_allowed = allowed  # type: ignore[method-assign]  # noqa: SLF001 — без БД
    for _ in range(3):
        assert await runtime._claim(1) == 1  # noqa: SLF001
        await asyncio.gather(*(entry.handle for entry in runtime._active.values()))  # noqa: SLF001
    assert busy.methods().count("claim") == 3
