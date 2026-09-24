"""Adversarial-перевірки WP-01D PR1c від незалежного тестувальника — без БД.

Доповнюють `test_runtime_plumbing.py`/`test_registry.py`/`test_handlers.py` сценаріями, які там
не покриті: `defer` на **останній** спробі, `not_before` у минулому для `retry`, межа стелі
clamp, скінченність і детермінізм `RetrySchedule` (jitter у межах, фіксований seed), роутинг
задач між кількома `HandlerBinding` з фабрики реєстру, ізоляція зламаного доменного модуля від
інших ролей, `output` на `crawl_jobs` → карантин без витоку значення output, ізоляція винятку
доменного тіку в `SchedulerRuntime._run_due_ticks`.
"""

from __future__ import annotations

import asyncio
import random
import sys
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from structlog.testing import capture_logs

from collector.contracts import new_entity_id
from collector.workers import registry
from collector.workers.backends import CrawlJobsBackend
from collector.workers.config import SchedulerRuntimeConfig, WorkerRuntimeConfig
from collector.workers.handlers import (
    HANDLER_FACTORIES,
    HandlerBinding,
    HandlerContext,
    NoopHandler,
    RetrySchedule,
    Task,
    TaskHandler,
    TaskResult,
)
from collector.workers.registry import DomainTickSpec, HandlerRegistryError, LoadedTick
from collector.workers.roles import WorkerRole
from collector.workers.runtime import DEFAULT_BACKOFF, WorkerRuntime
from collector.workers.scheduler import MAINTENANCE_TICK, SchedulerRuntime, TickContext

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


def make_task(job_type: str = "fetch.http", *, attempt: int = 1, max_attempts: int = 4) -> Task:
    return Task(
        job_id=new_entity_id(),
        job_type=job_type,
        args={"url": "https://example.test/?token=s3cr3t"},
        attempt=attempt,
        max_attempts=max_attempts,
        priority=100,
        not_before=NOW,
        run_id=None,
        source_id=None,
    )


@dataclass
class RecordingBackend:
    """`QueueBackend`, що записує виклики; `claim` видає tasks типу першого job_type."""

    label: str = "fake"
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.label

    def check_output(self, output: object) -> None:
        return None

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
        tasks = [make_task(job_types[0]) for _ in range(limit)]
        self.calls.append(("claim", {"job_ids": [task.job_id for task in tasks]}))
        return tasks

    async def heartbeat(
        self, session: object, job_id: UUID, owner: str, lease_seconds: int, *, now: datetime
    ) -> None:
        return None

    async def complete(
        self, session: object, task: Task, owner: str, output: object, *, now: datetime
    ) -> None:
        self.calls.append(("complete", {"job_id": task.job_id, "job_type": task.job_type}))

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
        self.calls.append(("retry", {"error_code": error_code, "not_before": not_before}))
        return "retry"

    async def defer(
        self, session: object, job_id: UUID, owner: str, *, until: datetime, now: datetime
    ) -> None:
        self.calls.append(("defer", {"until": until}))

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
        self.calls.append(("quarantine", {"error_code": error_code, "message": error_message}))

    async def release(self, session: object, job_id: UUID, owner: str, *, now: datetime) -> None:
        self.calls.append(("release", {"job_id": job_id}))

    async def recover_expired(self, session: object, *, limit: int, now: datetime) -> list[UUID]:
        return []


class Handler(TaskHandler):
    def __init__(
        self,
        job_type: str = "fetch.http",
        *,
        schedule: RetrySchedule | None = None,
        result: TaskResult | None = None,
    ) -> None:
        self._job_type = job_type
        self._schedule = schedule
        self._result = result or TaskResult.success()
        self.handled: list[Task] = []

    @property
    def job_types(self) -> tuple[str, ...]:
        return (self._job_type,)

    @property
    def retry_schedule(self) -> RetrySchedule | None:
        return self._schedule

    async def handle(self, task: Task) -> TaskResult:
        self.handled.append(task)
        return self._result


def runtime_for(
    *bindings: HandlerBinding,
    role: WorkerRole = WorkerRole.FETCH,
    max_defer_seconds: float = 24 * 3600,
    rng: random.Random | None = None,
) -> WorkerRuntime:
    return WorkerRuntime(
        WorkerRuntimeConfig(role=role, max_defer_seconds=max_defer_seconds),
        cast("Any", fake_sessions),
        list(bindings) if bindings else None,
        clock=lambda: NOW,
        rng=rng or random.Random(0),  # noqa: S311 — детермінізм тесту
    )


async def report(
    result: TaskResult,
    *,
    task: Task | None = None,
    schedule: RetrySchedule | None = None,
    backend: Any = None,
    max_defer_seconds: float = 24 * 3600,
) -> Any:
    queue = backend if backend is not None else RecordingBackend()
    binding = HandlerBinding(cast("Any", queue), Handler(schedule=schedule))
    runtime = runtime_for(binding, max_defer_seconds=max_defer_seconds)
    await runtime._report(task or make_task(), binding, result)  # noqa: SLF001
    assert (runtime.reports, runtime.report_failures) == (1, 0)
    return queue


# --- defer: не спалює спробу навіть на останній -------------------------------------------


async def test_defer_on_the_last_attempt_is_a_release_not_a_retry_or_quarantine() -> None:
    """`attempt == max_attempts`: retry дав би карантин + dead letter; defer — лише release."""
    until = NOW + timedelta(minutes=3)
    queue = await report(
        TaskResult.deferred(until, "permit_denied"), task=make_task(attempt=4, max_attempts=4)
    )
    assert queue.calls == [("defer", {"until": until})]


async def test_defer_log_carries_no_task_text_and_no_error_fields() -> None:
    with capture_logs() as logs:
        await report(TaskResult.deferred(NOW + timedelta(minutes=1), "source_paused"))
    deferred = [entry for entry in logs if entry["event"] == "worker.task_deferred"]
    assert len(deferred) == 1
    flat = repr(logs)
    assert "s3cr3t" not in flat and "example.test" not in flat, "текст/args задачі не в лозі"
    assert "error_message" not in deferred[0] and "error" not in deferred[0]


def test_deferred_result_has_no_error_message_by_construction() -> None:
    result = TaskResult.deferred(NOW, "rate_limited")
    assert (result.disposition, result.error_message, result.output) == ("defer", None, None)


# --- retry: not_before у минулому / на межі стелі -------------------------------------------


async def test_retry_lower_bound_in_the_past_does_not_shorten_the_schedule() -> None:
    queue = await report(
        TaskResult.retryable("http_429", not_before=NOW - timedelta(days=1)), schedule=SPEC_10
    )
    assert queue.calls == [
        ("retry", {"error_code": "http_429", "not_before": NOW + SPEC_10.delays[0]})
    ]


async def test_retry_lower_bound_in_the_past_without_schedule_uses_default_backoff() -> None:
    queue = await report(TaskResult.retryable("http_429", not_before=NOW - timedelta(hours=1)))
    not_before = queue.calls[0][1]["not_before"]
    base = DEFAULT_BACKOFF.base
    assert NOW + base <= not_before <= NOW + base * (1 + DEFAULT_BACKOFF.jitter_ratio)


async def test_defer_exactly_at_the_ceiling_is_kept_and_one_microsecond_more_is_clamped() -> None:
    ceiling = NOW + timedelta(hours=1)
    with capture_logs() as logs:
        queue = await report(TaskResult.deferred(ceiling, "budget"), max_defer_seconds=3600)
    assert queue.calls == [("defer", {"until": ceiling})]
    assert not [entry for entry in logs if entry["event"] == "worker.not_before_clamped"]

    with capture_logs() as logs:
        queue = await report(
            TaskResult.deferred(ceiling + timedelta(microseconds=1), "budget"),
            max_defer_seconds=3600,
        )
    assert queue.calls == [("defer", {"until": ceiling})]
    assert [entry["event"] for entry in logs].count("worker.not_before_clamped") == 1


async def test_far_future_retry_lower_bound_with_a_long_schedule_step_stays_under_ceiling() -> None:
    """Розклад довший за стелю → `not_before = now + delay` (стеля — лише для нижньої межі)."""
    long = RetrySchedule((timedelta(hours=48),))
    queue = await report(
        TaskResult.retryable("http_429", not_before=NOW + timedelta(days=365)),
        schedule=long,
        max_defer_seconds=3600,
    )
    assert queue.calls[0][1]["not_before"] == NOW + timedelta(hours=48)


# --- RetrySchedule: скінченність, jitter у межах, детермінізм --------------------------------


@pytest.mark.parametrize("attempt", [-10, 0, 1])
def test_schedule_before_the_first_attempt_uses_the_first_step(attempt: int) -> None:
    assert SPEC_10.delay(attempt, random.Random(0)) == timedelta(seconds=5)  # noqa: S311


@pytest.mark.parametrize("attempt", [4, 5, 100, 10**9])
def test_schedule_is_finite_and_sticks_to_the_last_step(attempt: int) -> None:
    assert SPEC_10.delay(attempt, random.Random(0)) == timedelta(minutes=10)  # noqa: S311


@pytest.mark.parametrize("ratio", [0.2, 1.0])
def test_schedule_jitter_stays_within_bounds_for_many_seeds(ratio: float) -> None:
    schedule = RetrySchedule(SPEC_10.delays, jitter_ratio=ratio)
    for seed in range(20):
        rng = random.Random(seed)  # noqa: S311
        for attempt in range(1, 8):
            base = SPEC_10.delays[min(attempt, 4) - 1]
            delay = schedule.delay(attempt, rng)
            assert base <= delay <= base * (1 + ratio), (seed, attempt, delay)
    worst = max(schedule.delay(n, random.Random(n)) for n in range(1, 1000))  # noqa: S311
    assert worst <= timedelta(minutes=10) * (1 + ratio), "стеля розкладу скінченна"


def test_schedule_is_deterministic_with_a_fixed_seed() -> None:
    schedule = RetrySchedule(SPEC_10.delays, jitter_ratio=0.2)
    first = [schedule.delay(n, random.Random(42)) for n in range(1, 6)]  # noqa: S311
    rng_a, rng_b = random.Random(42), random.Random(42)  # noqa: S311
    seq_a = [schedule.delay(n, rng_a) for n in range(1, 6)]
    seq_b = [schedule.delay(n, rng_b) for n in range(1, 6)]
    assert seq_a == seq_b
    assert first == [schedule.delay(n, random.Random(42)) for n in range(1, 6)]  # noqa: S311
    assert len(set(seq_a)) > 1, "jitter справді працює"


async def test_runtime_retry_not_before_is_deterministic_with_a_seeded_rng() -> None:
    schedule = RetrySchedule(SPEC_10.delays, jitter_ratio=0.2)

    async def sequence(seed: int) -> list[datetime]:
        queue = RecordingBackend()
        binding = HandlerBinding(cast("Any", queue), Handler(schedule=schedule))
        runtime = runtime_for(binding, rng=random.Random(seed))  # noqa: S311
        for attempt in range(1, 5):
            await runtime._report(  # noqa: SLF001
                make_task(attempt=attempt), binding, TaskResult.retryable("http_503")
            )
        return [kwargs["not_before"] for _, kwargs in queue.calls]

    first, second = await sequence(7), await sequence(7)
    assert first == second
    for attempt, value in enumerate(first, start=1):
        base = SPEC_10.delays[attempt - 1]
        assert NOW + base <= value <= NOW + base * 1.2


# --- output на crawl_jobs → карантин без витоку значення ------------------------------------


class RecordingCrawlJobs(CrawlJobsBackend):
    """Справжній `check_output` `crawl_jobs`, але запис у чергу — у список."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def complete(
        self, session: Any, task: Task, owner: str, output: object, *, now: datetime
    ) -> None:
        self.calls.append(("complete", {}))

    async def quarantine(
        self,
        session: Any,
        job_id: UUID,
        owner: str,
        *,
        error_code: str,
        error_message: str | None,
        now: datetime,
    ) -> None:
        self.calls.append(("quarantine", {"error_code": error_code, "message": error_message}))


async def test_crawl_jobs_output_is_quarantined_and_the_value_does_not_leak() -> None:
    backend = RecordingCrawlJobs()
    with capture_logs() as logs:
        await report(TaskResult.success(output={"api_key": "leak-me"}), backend=backend)
    ((method, kwargs),) = backend.calls
    assert (method, kwargs["error_code"]) == ("quarantine", "invalid_handler_output")
    assert "leak-me" not in (kwargs["message"] or "")
    assert "leak-me" not in repr(logs)


# --- кілька прив'язок з фабрики реєстру: роутинг --------------------------------------------


@pytest.fixture
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(registry, "ROLE_HANDLER_MODULES", {})
    saved = dict(HANDLER_FACTORIES)
    HANDLER_FACTORIES.clear()
    try:
        yield
    finally:
        HANDLER_FACTORIES.clear()
        HANDLER_FACTORIES.update(saved)


async def test_factory_bindings_route_each_task_to_its_own_handler_and_queue(
    clean_registry: None,
) -> None:
    projection, crawl = RecordingBackend("projection_tasks"), RecordingBackend("crawl_jobs")
    projector = Handler("catalog_items")
    reconcile = Handler("projection.reconcile", result=TaskResult.retryable("boom"))
    contexts: list[HandlerContext] = []

    def factory(ctx: HandlerContext) -> list[HandlerBinding]:
        contexts.append(ctx)
        return [
            HandlerBinding(cast("Any", projection), projector),
            HandlerBinding(cast("Any", crawl), reconcile),
        ]

    HANDLER_FACTORIES[WorkerRole.PROJECTOR] = factory
    runtime = runtime_for(role=WorkerRole.PROJECTOR)
    assert [binding.handler for binding in runtime.bindings] == [projector, reconcile]
    assert contexts[0].worker_instance_id == runtime.instance_id

    async def allowed(_session: object) -> bool:
        return True

    runtime._claim_allowed = allowed  # type: ignore[method-assign]  # noqa: SLF001 — без БД
    for _ in range(6):
        assert await runtime._claim(1) == 1  # noqa: SLF001
        await asyncio.gather(*(entry.handle for entry in runtime._active.values()))  # noqa: SLF001

    assert {task.job_type for task in projector.handled} == {"catalog_items"}
    assert {task.job_type for task in reconcile.handled} == {"projection.reconcile"}
    assert len(projector.handled) == len(reconcile.handled) == 3
    # Звіт іде в чергу, з якої task узято, і лише туди.
    assert [kw["job_id"] for m, kw in projection.calls if m == "complete"] == [
        task.job_id for task in projector.handled
    ]
    assert crawl.methods().count("retry") == 3 and "complete" not in crawl.methods()
    assert "retry" not in projection.methods()


# --- lazy import: зламаний модуль однієї ролі не валить інші ---------------------------------


BROKEN_MODULE = """
from collector.workers.handlers import HANDLER_FACTORIES, NoopHandler
from collector.workers.roles import WorkerRole

# Реєструє фабрику і лише потім падає: напівімпортований модуль не має дати робочий handler.
HANDLER_FACTORIES[WorkerRole.TRANSLATION] = NoopHandler
raise RuntimeError("kaboom: provider SDK missing credentials")
"""

GOOD_MODULE = """
from collector.workers.handlers import HANDLER_FACTORIES, Task, TaskHandler, TaskResult
from collector.workers.roles import WorkerRole


class FetchHandler(TaskHandler):
    def __init__(self, ctx):
        self.ctx = ctx

    @property
    def job_types(self):
        return ("fetch.http",)

    async def handle(self, task: Task) -> TaskResult:
        return TaskResult.success()


HANDLER_FACTORIES[WorkerRole.FETCH] = FetchHandler
"""


def test_broken_module_of_one_role_gives_a_clear_error_and_does_not_affect_other_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_registry: None
) -> None:
    package = f"wp01d_adv_{uuid4().hex}"
    (tmp_path / package).mkdir()
    (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / package / "translation.py").write_text(BROKEN_MODULE, encoding="utf-8")
    (tmp_path / package / "fetch.py").write_text(GOOD_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        registry,
        "ROLE_HANDLER_MODULES",
        {
            WorkerRole.TRANSLATION: f"{package}.translation",
            WorkerRole.FETCH: f"{package}.fetch",
        },
    )
    try:
        for _ in range(2):  # повторний boot теж падає, а не бере напівімпортовану фабрику
            with pytest.raises(HandlerRegistryError) as info:
                runtime_for(role=WorkerRole.TRANSLATION)
            message = str(info.value)
            assert f"{package}.translation" in message
            assert "RuntimeError" in message and "kaboom" in message
            assert isinstance(info.value.__cause__, RuntimeError)
            assert f"{package}.translation" not in sys.modules

        fetch = runtime_for(role=WorkerRole.FETCH)
        assert type(fetch.handler).__name__ == "FetchHandler"
        parse = runtime_for(role=WorkerRole.PARSE)
        assert type(parse.handler) is NoopHandler
    finally:
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]


# --- scheduler: виняток доменного тіку ізольований ------------------------------------------


def make_scheduler(*ticks: LoadedTick, maintenance: list[str]) -> SchedulerRuntime:
    async def fake_maintenance(_session: object, _now: datetime) -> None:
        maintenance.append("run")

    scheduler = SchedulerRuntime(
        SchedulerRuntimeConfig(lease_name="adv", tick_seconds=0.01, lease_retry_seconds=0.01),
        cast("Any", object()),
        cast("Any", fake_sessions),
        tick=fake_maintenance,
        domain_ticks=list(ticks),
        environ={},
    )
    scheduler._active = True  # noqa: SLF001 — без advisory lease
    return scheduler


def loaded(name: str, run: Any, timeout: float = 5.0) -> LoadedTick:
    return LoadedTick(DomainTickSpec(name, f"tests:{name}", 0.01, timeout), run)


async def test_exception_in_a_domain_tick_does_not_stop_the_next_ticks_or_passes() -> None:
    order: list[str] = []
    maintenance: list[str] = []

    async def boom(_ctx: TickContext) -> None:
        order.append("boom")
        msg = "tick exploded"
        raise ValueError(msg)

    async def after(_ctx: TickContext) -> None:
        order.append("after")

    scheduler = make_scheduler(
        loaded("boom", boom), loaded("after", after), maintenance=maintenance
    )

    async def held() -> bool:
        return True

    scheduler._lease_held = held  # type: ignore[method-assign]  # noqa: SLF001
    with capture_logs() as logs:
        for _ in range(3):
            for item in scheduler._ticks:  # noqa: SLF001
                item.next_due = 0.0
            await scheduler._run_due_ticks()  # noqa: SLF001
    assert order == ["boom", "after"] * 3
    assert len(maintenance) == 3
    assert scheduler.tick_failures == {"boom": 3}
    assert scheduler.tick_runs == {MAINTENANCE_TICK: 3, "after": 3}
    assert scheduler.is_active
    failed = [entry for entry in logs if entry["event"] == "scheduler.tick_failed"]
    assert {entry["tick"] for entry in failed} == {"boom"}


async def test_lease_lost_mid_pass_stops_the_remaining_domain_ticks() -> None:
    order: list[str] = []
    maintenance: list[str] = []

    async def record(_ctx: TickContext) -> None:
        order.append("domain")

    scheduler = make_scheduler(loaded("domain", record), maintenance=maintenance)

    async def lost() -> bool:
        return False

    scheduler._lease_held = lost  # type: ignore[method-assign]  # noqa: SLF001
    await scheduler._run_due_ticks()  # noqa: SLF001
    assert maintenance == ["run"]
    assert order == [], "без lease доменний тік не виконується"
    assert not scheduler.is_active and scheduler.lease_losses == 1


async def test_cancelled_lease_check_keeps_connection_lock_until_query_finishes() -> None:
    """`shield` не має відпускати lock, доки detached запит ще використовує connection."""

    class SlowLease:
        def __init__(self) -> None:
            self.calls = 0
            self.active = 0
            self.max_active = 0
            self.first_started = asyncio.Event()
            self.finish_first = asyncio.Event()

        async def is_held(self) -> bool:
            self.calls += 1
            call = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                if call == 1:
                    self.first_started.set()
                    await self.finish_first.wait()
                return True
            finally:
                self.active -= 1

    scheduler = make_scheduler(maintenance=[])
    lease = SlowLease()
    scheduler.lease = cast("Any", lease)

    first = asyncio.create_task(scheduler._lease_held())  # noqa: SLF001
    await lease.first_started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(scheduler._lease_held())  # noqa: SLF001
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert lease.calls == 1
    assert not second.done()

    lease.finish_first.set()
    assert await second is True
    assert lease.calls == 2
    assert lease.max_active == 1
