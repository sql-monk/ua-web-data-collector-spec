"""Транзакційний audit control plane (§13; знахідка S-2 пострев'ю PR1, картка PR2).

Операції, для яких у PR1 audit був обов'язком викликача, тепер пишуть `audit_log` усередині
репозиторію, у тій самій транзакції: `set_source_state`, `add_policy_version`,
`upsert_route`/`set_route_state`, `upsert_cursor`, `block_origin`, `quarantine(owner=None)`,
`upsert_pool`. Перевіряється, що mutating-операція без audit-запису неможлива:

- сигнатура: `actor`/`reason` — обов'язкові keyword-параметри без default;
- порожні `actor`/`reason` → `InvalidValueError` до першого запису (нічого не змінено);
- кожен виклик лишає рівно один audit-рядок із before/after, а відкат транзакції прибирає
  і зміну, і слід разом.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts.enums import DataDomain, RouteState, SourceState
from collector.persistence.postgres.errors import InvalidValueError
from collector.persistence.postgres.models import AuditLog, CrawlJob, Source
from collector.persistence.postgres.repositories import limiter, pools, queue, sources
from collector.workers.roles import WorkerRole

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
ORIGIN = "https://news.example.test"
POLICY = sources.PolicySnapshot(
    requests_per_second=Decimal("0.5"),
    max_concurrency=2,
    crawl_interval_seconds=3600,
    robots_policy="respect",
    manifest_sha256="a" * 64,
)
AUDITED_OPERATIONS: list[Callable[..., Awaitable[Any]]] = [
    sources.set_source_state,
    sources.add_policy_version,
    sources.upsert_route,
    sources.set_route_state,
    sources.upsert_cursor,
    limiter.block_origin,
    pools.upsert_pool,
]


class _Rollback(Exception):
    """Вихід із транзакції без commit."""


@pytest.mark.parametrize("operation", AUDITED_OPERATIONS, ids=lambda op: op.__name__)
def test_actor_and_reason_are_required_keyword_arguments(
    operation: Callable[..., Awaitable[Any]],
) -> None:
    parameters = inspect.signature(operation).parameters
    for name in ("actor", "reason"):
        parameter = parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (operation.__name__, name)
        assert parameter.default is inspect.Parameter.empty, (operation.__name__, name)


async def _audits(session: AsyncSession, action: str | None = None) -> list[AuditLog]:
    # Однаковий fake-час → порядок за UUIDv7 `audit_id` (монотонний у межах процесу).
    stmt = select(AuditLog).order_by(AuditLog.created_at, AuditLog.audit_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    return list(await session.scalars(stmt))


async def _source(session: AsyncSession) -> Source:
    async with session.begin():
        return await sources.create_source(
            session, source_id="news_ua_example", domain=DataDomain.NEWS, country="UA", now=T0
        )


async def test_every_listed_mutation_writes_exactly_one_audit_row(
    pg_session: AsyncSession,
) -> None:
    source = await _source(pg_session)
    async with pg_session.begin():
        await sources.set_source_state(
            pg_session,
            source.id,
            SourceState.ENABLED,
            expected_revision=1,
            actor="op",
            reason="launch",
            request_id="req-1",
            now=T0,
        )
        await sources.add_policy_version(
            pg_session, source.id, POLICY, expected_revision=2, actor="op", reason="p1", now=T0
        )
        route = await sources.upsert_route(
            pg_session, source.id, "rss", "https://x.test/rss", actor="disc", reason="new", now=T0
        )
        # Повтор для наявного route нічого не змінює — і сліду не лишає.
        await sources.upsert_route(
            pg_session, source.id, "rss", "https://x.test/rss", actor="disc", reason="new", now=T0
        )
        await sources.set_route_state(
            pg_session,
            route.id,
            RouteState.CIRCUIT_OPEN,
            expected_revision=1,
            actor="fetch",
            reason="5xx",
            now=T0,
        )
        # etag-1 → той самий etag-1 (без мутації, без сліду, CR-9) → etag-2: рівно 2 audit-рядки.
        for value in ("etag-1", "etag-1", "etag-2"):
            await sources.upsert_cursor(
                pg_session,
                source.id,
                "rss",
                "https://x.test/rss",
                value,
                actor="disc",
                reason="discovery tick",
                now=T0,
            )
        await limiter.ensure_bucket(
            pg_session, ORIGIN, capacity_tokens=2, refill_per_second="0.5", max_concurrency=1
        )
        until = T0 + timedelta(hours=1)
        await limiter.block_origin(
            pg_session, ORIGIN, until, actor="fetch", reason="429 Retry-After", now=T0
        )
        # Коротший block усередині чинного вікна нічого не змінює — сліду немає.
        await limiter.block_origin(
            pg_session, ORIGIN, T0 + timedelta(minutes=1), actor="fetch", reason="429", now=T0
        )
        job = await queue.enqueue(pg_session, queue.NewJob(job_type="fetch", idempotency_key="j"))
        await queue.quarantine(
            pg_session, job.job_id, None, error_code="manual", actor="op", reason="bad", now=T0
        )
        state = pools.PoolDesiredState(desired_replicas=1, desired_concurrency=2, max_replicas=4)
        pool = await pools.upsert_pool(
            pg_session,
            WorkerRole.FETCH,
            state,
            actor="worker:boot",
            reason="bootstrap",
            expected_revision=None,
            now=T0,
        )
        await pools.upsert_pool(
            pg_session,
            WorkerRole.FETCH,
            pools.PoolDesiredState(desired_replicas=2, desired_concurrency=2, max_replicas=4),
            actor="admin",
            reason="more",
            expected_revision=pool.revision,
            now=T0,
        )
        audits = await _audits(pg_session)
    assert [a.action for a in audits] == [
        "source.set_state",
        "source.add_policy_version",
        "source_route.create",
        "source_route.set_state",
        "source_cursor.upsert",
        "source_cursor.upsert",
        "origin.block",
        "crawl_job.quarantine",
        "worker_pool.create",
        "worker_pool.update",
    ]
    by_action = {a.action: a for a in audits}
    set_state = by_action["source.set_state"]
    assert (set_state.actor, set_state.request_id) == ("op", "req-1")
    assert set_state.before_state == {"state": "paused", "revision": 1}
    assert set_state.after_state == {"state": "enabled", "revision": 2, "reason": "launch"}
    pool_update = by_action["worker_pool.update"]
    assert pool_update.before_state is not None and pool_update.after_state is not None
    assert pool_update.before_state["desired_replicas"] == 1
    assert pool_update.after_state["desired_replicas"] == 2
    assert by_action["origin.block"].after_state == {
        "blocked_until": (T0 + timedelta(hours=1)).isoformat(),
        "reason": "429 Retry-After",
    }


async def test_rollback_removes_change_and_audit_together(pg_session: AsyncSession) -> None:
    source = await _source(pg_session)
    with pytest.raises(_Rollback):
        async with pg_session.begin():
            await sources.set_source_state(
                pg_session,
                source.id,
                SourceState.DISABLED,
                expected_revision=1,
                actor="op",
                reason="incident",
                now=T0,
            )
            assert len(await _audits(pg_session, "source.set_state")) == 1
            raise _Rollback
    async with pg_session.begin():
        found = await sources.get_source(pg_session, "news_ua_example")
        audits = await _audits(pg_session)
    assert found is not None and (found.state, found.revision) == ("paused", 1)
    assert audits == []


@pytest.mark.parametrize(("actor", "reason"), [("", "why"), ("op", ""), ("  ", "why")])
async def test_blank_actor_or_reason_is_rejected_before_any_write(
    pg_session: AsyncSession, actor: str, reason: str
) -> None:
    source = await _source(pg_session)
    async with pg_session.begin():
        with pytest.raises(InvalidValueError, match="audit"):
            await sources.set_source_state(
                pg_session,
                source.id,
                SourceState.DISABLED,
                expected_revision=1,
                actor=actor,
                reason=reason,
                now=T0,
            )
        with pytest.raises(InvalidValueError, match="audit"):
            await sources.upsert_cursor(
                pg_session, source.id, "rss", "k", "v", actor=actor, reason=reason, now=T0
            )
        with pytest.raises(InvalidValueError, match="audit"):
            await limiter.block_origin(pg_session, ORIGIN, T0, actor=actor, reason=reason, now=T0)
        # Транзакція викликача не зіпсована — у ній можна продовжувати.
        found = await sources.get_source(pg_session, "news_ua_example")
        audit_count = await pg_session.scalar(select(func.count()).select_from(AuditLog))
    assert found is not None and (found.state, found.revision) == ("paused", 1)
    assert audit_count == 0


async def test_operator_quarantine_requires_actor_and_reason(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        job = await queue.enqueue(pg_session, queue.NewJob(job_type="fetch", idempotency_key="j"))
        with pytest.raises(InvalidValueError):
            await queue.quarantine(pg_session, job.job_id, None, error_code="manual", now=T0)
        stored = await pg_session.get(CrawlJob, job.job_id)
    assert stored is not None and stored.status == "pending"


async def test_operator_quarantine_rejects_blank_actor_like_other_audited_operations(
    pg_session: AsyncSession,
) -> None:
    """Gate 3, CR-8: `quarantine(owner=None)` перевіряє `require_audit_context` (пробіли — ні)."""
    async with pg_session.begin():
        job = await queue.enqueue(pg_session, queue.NewJob(job_type="fetch", idempotency_key="j"))
        with pytest.raises(InvalidValueError, match="audit"):
            await queue.quarantine(
                pg_session, job.job_id, None, error_code="manual", actor="  ", reason="x", now=T0
            )
        with pytest.raises(InvalidValueError, match="audit"):
            await queue.quarantine(
                pg_session, job.job_id, None, error_code="manual", actor="op", reason=" ", now=T0
            )


async def test_unchanged_cursor_is_neither_updated_nor_audited(pg_session: AsyncSession) -> None:
    """Gate 3, CR-9: той самий cursor — не мутація: revision і `updated_at` незмінні."""
    source = await _source(pg_session)
    async with pg_session.begin():
        first = await sources.upsert_cursor(
            pg_session, source.id, "rss", "k", "v1", actor="d", reason="tick", now=T0
        )
        revision, updated_at = first.revision, first.updated_at
        same = await sources.upsert_cursor(
            pg_session,
            source.id,
            "rss",
            "k",
            "v1",
            actor="d",
            reason="tick",
            now=T0 + timedelta(minutes=5),
        )
        audits = await _audits(pg_session, "source_cursor.upsert")
    assert (same.id, same.revision, same.updated_at) == (first.id, revision, updated_at)
    assert len(audits) == 1
