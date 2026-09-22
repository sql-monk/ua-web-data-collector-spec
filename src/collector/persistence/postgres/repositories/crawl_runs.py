"""Crawl runs FR-002: `start_run` / `finish_run`.

Один running повний обхід на джерело гарантує partial unique index
`uq_crawl_runs_running_full (source_id) WHERE status='running' AND kind='full'`; `start_run`
використовує `INSERT ... ON CONFLICT ... DO NOTHING`, тож конфлікт не ламає транзакцію
викликача, а повертає `ConflictError`.

Transaction boundary: викликач.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import new_entity_id
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import (
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
)
from collector.persistence.postgres.models import CRAWL_RUN_KINDS, CrawlRun

FINISHED_RUN_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


async def start_run(
    session: AsyncSession,
    source_id: UUID,
    kind: str,
    *,
    started_by: str,
    policy_version_id: UUID | None = None,
    now: datetime | None = None,
) -> CrawlRun:
    """Створює `running` run; другий `full` run для джерела з уже running `full` →
    `ConflictError` (інші kinds не обмежуються)."""
    if kind not in CRAWL_RUN_KINDS:
        msg = f"невідомий kind {kind!r}; дозволені {CRAWL_RUN_KINDS}"
        raise ValueError(msg)
    current = resolve_now(now)
    stmt = (
        pg_insert(CrawlRun)
        .values(
            id=new_entity_id(),
            source_id=source_id,
            policy_version_id=policy_version_id,
            kind=kind,
            status="running",
            started_by=started_by,
            started_at=current,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing(
            index_elements=[CrawlRun.source_id],
            index_where=text("status = 'running' AND kind = 'full'"),
        )
        .returning(CrawlRun)
    )
    run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        msg = f"джерело {source_id} уже має running full crawl run"
        raise ConflictError(msg)
    return run


async def finish_run(
    session: AsyncSession,
    run_id: UUID,
    status: str,
    *,
    error_code: str | None = None,
    now: datetime | None = None,
) -> CrawlRun:
    """`running` → `succeeded | failed | cancelled`; повторний finish → `InvalidTransitionError`."""
    if status not in FINISHED_RUN_STATUSES:
        msg = f"status має бути одним із {sorted(FINISHED_RUN_STATUSES)}, отримано {status!r}"
        raise ValueError(msg)
    run = await session.get(CrawlRun, run_id, with_for_update=True)
    if run is None:
        msg = f"crawl run {run_id} не знайдено"
        raise NotFoundError(msg)
    if run.status != "running":
        msg = f"crawl run {run_id}: перехід {run.status} → {status} недозволений"
        raise InvalidTransitionError(msg)
    current = resolve_now(now)
    run.status = status
    run.error_code = error_code
    run.finished_at = current
    run.updated_at = current
    await session.flush()
    return run


async def get_run(session: AsyncSession, run_id: UUID) -> CrawlRun | None:
    """Один crawl run за PK. Transaction boundary: викликач; один SELECT, без блокування."""
    return await session.get(CrawlRun, run_id)
