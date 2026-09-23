"""Unit-тести контракту handler-ів (`Task`, `TaskResult`, `NoopHandler`, реєстр) — WP-01D PR1."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from collector.contracts import new_entity_id
from collector.workers.handlers import (
    HANDLER_FACTORIES,
    NoopHandler,
    PermanentTaskError,
    Task,
    TaskHandler,
    TaskResult,
    check_handler_contract,
    redact,
    resolve_handler,
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


@pytest.fixture
def clean_registry() -> Iterator[None]:
    saved = dict(HANDLER_FACTORIES)
    try:
        yield
    finally:
        HANDLER_FACTORIES.clear()
        HANDLER_FACTORIES.update(saved)


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


def test_resolve_handler_falls_back_to_noop(clean_registry: None) -> None:
    HANDLER_FACTORIES.clear()
    for role in WorkerRole:
        assert isinstance(resolve_handler(role), NoopHandler)


def test_resolve_handler_uses_registered_domain_handler(clean_registry: None) -> None:
    class DomainHandler(TaskHandler):
        @property
        def job_types(self) -> tuple[str, ...]:
            return ("fetch.http", "fetch.head")

        async def handle(self, task: Task) -> TaskResult:
            return TaskResult.success()

    HANDLER_FACTORIES[WorkerRole.FETCH] = lambda _role: DomainHandler()
    handler = resolve_handler(WorkerRole.FETCH)
    assert isinstance(handler, DomainHandler)
    assert handler.job_types == ("fetch.http", "fetch.head")
    assert isinstance(resolve_handler(WorkerRole.PARSE), NoopHandler)


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
