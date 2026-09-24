"""Unit-тести контракту handler-ів (`Task`, `TaskResult`, `RetrySchedule`, `NoopHandler`) — WP-01D.

PR1: базовий контракт; PR1c: `defer`, `retry(not_before=)`, `output`, `RetrySchedule`. Реєстр
і lazy import — `test_registry.py`.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from collector.contracts import new_entity_id
from collector.workers.handlers import (
    NoopHandler,
    PermanentTaskError,
    RetrySchedule,
    Task,
    TaskHandler,
    TaskResult,
    check_handler_contract,
    redact,
    result_for_exception,
)
from collector.workers.roles import WorkerRole


def make_task(job_type: str = "fetch") -> Task:
    return Task(
        job_id=new_entity_id(),
        job_type=job_type,
        args={"url": "https://example.test/a"},
        attempt=1,
        max_attempts=5,
        priority=100,
        not_before=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        run_id=None,
        source_id=None,
    )


def test_success_result_has_no_error_code() -> None:
    assert TaskResult.success() == TaskResult(disposition="complete")
    with pytest.raises(ValueError, match="error_code"):
        TaskResult(disposition="complete", error_code="oops")


@pytest.mark.parametrize("disposition", ["retry", "quarantine"])
def test_failure_result_requires_error_code(disposition: str) -> None:
    with pytest.raises(ValueError, match="error_code"):
        TaskResult(disposition=disposition)  # type: ignore[arg-type]  # навмисно невалідне


def test_error_code_length_is_bounded() -> None:
    with pytest.raises(ValueError, match="довший"):
        TaskResult.retryable("e" * 65)


async def test_noop_handler_claims_role_job_type_and_succeeds() -> None:
    handler = NoopHandler(WorkerRole.PROJECTOR)
    assert handler.job_types == ("projector",)
    await handler.check_ready()
    assert await handler.handle(make_task("projector")) == TaskResult.success()


def test_unexpected_exception_is_retryable() -> None:
    result = result_for_exception(TimeoutError("з'єднання впало"))
    assert result.disposition == "retry"
    assert result.error_code == "handler_error"
    assert "TimeoutError" in (result.error_message or "")


def test_permanent_error_quarantines_with_its_own_code() -> None:
    result = result_for_exception(PermanentTaskError("robots disallow", error_code="robots_denied"))
    assert (result.disposition, result.error_code) == ("quarantine", "robots_denied")


def test_sync_handler_is_rejected_because_it_would_block_the_event_loop() -> None:
    """M-2: синхронний `handle` знімає heartbeat, self-fencing і drain одночасно."""

    class BlockingHandler(TaskHandler):
        @property
        def job_types(self) -> tuple[str, ...]:
            return ("fetch",)

        def handle(self, task: Task) -> TaskResult:  # type: ignore[override]  # навмисно sync
            return TaskResult.success()

    with pytest.raises(TypeError, match="async def"):
        check_handler_contract(BlockingHandler())


def test_handler_without_job_types_is_rejected() -> None:
    """Порожній `job_types` = worker мовчки простоює вічно (`claim` завжди повертає [])."""

    class SilentHandler(TaskHandler):
        @property
        def job_types(self) -> tuple[str, ...]:
            return ()

        async def handle(self, task: Task) -> TaskResult:
            return TaskResult.success()

    with pytest.raises(ValueError, match="job_types"):
        check_handler_contract(SilentHandler())


def test_noop_handler_satisfies_the_contract() -> None:
    check_handler_contract(NoopHandler(WorkerRole.FETCH))


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "InvalidURL: https://user:s3cret@example.test/a",
            "InvalidURL: https://[redacted]@example.test/a",
        ),
        ("HTTPError: /a?token=abc123&x=1", "HTTPError: /a?token=[redacted]&x=1"),
        ("HTTPError: /a?api_key=zzz", "HTTPError: /a?api_key=[redacted]"),
        ("ValueError: нічого секретного", "ValueError: нічого секретного"),
    ],
)
def test_error_text_is_redacted_before_it_reaches_the_queue(message: str, expected: str) -> None:
    """§13: тексти винятків ідуть у `crawl_jobs`/`dead_letters`/логи — секрети вирізаються."""
    assert redact(message) == expected
    assert redact(message) == redact(redact(message)), "редакція ідемпотентна"


def test_result_for_exception_redacts_too() -> None:
    result = result_for_exception(ValueError("https://u:p@example.test/x?password=qq"))
    assert "p@example.test" not in (result.error_message or "")
    assert "[redacted]" in (result.error_message or "")


# --- PR1c: defer, not_before, output, RetrySchedule ------------------------------------------

UNTIL = datetime(2026, 9, 22, 12, 10, tzinfo=UTC)


def test_deferred_carries_until_and_code_and_no_error_text() -> None:
    result = TaskResult.deferred(UNTIL, "rate_limited")
    assert (result.disposition, result.not_before, result.error_code) == (
        "defer",
        UNTIL,
        "rate_limited",
    )
    assert result.error_message is None


@pytest.mark.parametrize("code", ["", None])
def test_deferred_without_error_code_is_rejected(code: str | None) -> None:
    with pytest.raises(ValueError, match="error_code"):
        TaskResult.deferred(UNTIL, code)  # type: ignore[arg-type]  # навмисно невалідне


def test_defer_without_until_is_rejected() -> None:
    with pytest.raises(ValueError, match="until"):
        TaskResult(disposition="defer", error_code="rate_limited")


def test_defer_does_not_accept_an_error_message_it_would_silently_drop() -> None:
    with pytest.raises(ValueError, match="error_message"):
        TaskResult(
            disposition="defer", error_code="rate_limited", error_message="x", not_before=UNTIL
        )


def test_retryable_accepts_a_lower_bound() -> None:
    result = TaskResult.retryable("http_429", "Retry-After", not_before=UNTIL)
    assert (result.disposition, result.not_before) == ("retry", UNTIL)


@pytest.mark.parametrize(
    "build",
    [
        lambda: TaskResult(disposition="complete", not_before=UNTIL),
        lambda: TaskResult(disposition="quarantine", error_code="x", not_before=UNTIL),
    ],
    ids=["complete", "quarantine"],
)
def test_not_before_only_for_retry_and_defer(build: object) -> None:
    with pytest.raises(ValueError, match="not_before"):
        build()  # type: ignore[operator]  # параметризовані фабрики


def test_naive_not_before_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TaskResult.retryable("x", not_before=datetime(2026, 9, 22, 12, 0))  # noqa: DTZ001


def test_output_only_for_complete() -> None:
    receipt = object()
    assert TaskResult.success(output=receipt).output is receipt
    with pytest.raises(ValueError, match="output"):
        TaskResult(disposition="retry", error_code="x", output=receipt)
    with pytest.raises(ValueError, match="output"):
        TaskResult(disposition="quarantine", error_code="x", output=receipt)


def test_success_rejects_an_error_message() -> None:
    with pytest.raises(ValueError, match="error_message"):
        TaskResult(disposition="complete", error_message="oops")


SPEC_10 = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)


def test_retry_schedule_is_the_table_by_attempt_and_sticks_to_the_last_step() -> None:
    schedule = RetrySchedule(SPEC_10)
    rng = random.Random(0)  # noqa: S311 — детермінізм тесту, не криптографія
    assert [schedule.delay(n, rng) for n in range(1, 7)] == [*SPEC_10, SPEC_10[-1], SPEC_10[-1]]
    assert schedule.delay(0, rng) == SPEC_10[0], "attempt < 1 → перший крок"


def test_retry_schedule_jitter_is_bounded_above() -> None:
    schedule = RetrySchedule(SPEC_10, jitter_ratio=0.2)
    rng = random.Random(1)  # noqa: S311 — детермінізм тесту, не криптографія
    for attempt, base in enumerate(SPEC_10, start=1):
        for _ in range(200):
            delay = schedule.delay(attempt, rng)
            assert base <= delay <= base * 1.2


@pytest.mark.parametrize(
    ("delays", "jitter"),
    [((), 0.0), ((timedelta(0),), 0.0), ((timedelta(seconds=1),), 1.5)],
    ids=["empty", "zero-delay", "jitter>1"],
)
def test_retry_schedule_rejects_invalid_tables(
    delays: tuple[timedelta, ...], jitter: float
) -> None:
    with pytest.raises(ValueError, match="RetrySchedule|jitter_ratio"):
        RetrySchedule(delays, jitter_ratio=jitter)


def test_handler_without_schedule_keeps_the_default_backoff() -> None:
    assert NoopHandler(WorkerRole.FETCH).retry_schedule is None
