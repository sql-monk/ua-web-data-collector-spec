"""Control plane джерел: sources (optimistic revision), policy versions, routes, cursors.

Transaction boundary: викликач. Усі UPDATE versioned-ресурсів приймають `expected_revision`
і кидають `StaleRevisionError`, якщо рядок змінено кимось іншим (§7.6 stale GUI action).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import new_entity_id
from collector.contracts.enums import DataDomain, RouteState, SourceState
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import (
    ConflictError,
    NotFoundError,
    StaleRevisionError,
)
from collector.persistence.postgres.models import (
    ROUTE_KINDS,
    Source,
    SourceCursor,
    SourcePolicyVersion,
    SourceRoute,
)


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """Policy-частина маніфесту джерела (Додаток B), що впливає на limiter і розклад."""

    requests_per_second: Decimal
    max_concurrency: int
    crawl_interval_seconds: int
    robots_policy: str
    manifest_sha256: str
    burst_tokens: int = 1
    browser_allowed: bool = False
    manifest_uri: str | None = None


async def create_source(
    session: AsyncSession,
    *,
    source_id: str,
    domain: DataDomain,
    country: str,
    state: SourceState = SourceState.PAUSED,
    actor: str | None = None,
    now: datetime | None = None,
) -> Source:
    """Новий рядок `sources` (revision=1); повторна реєстрація `source_id` → `ConflictError`,
    а не сирий `IntegrityError`, який псує транзакцію викликача (L-2 код-рев'ю)."""
    current = resolve_now(now)
    if await session.scalar(select(Source.id).where(Source.source_id == source_id)):
        msg = f"джерело {source_id!r} уже зареєстроване"
        raise ConflictError(msg)
    source = Source(
        id=new_entity_id(),
        source_id=source_id,
        domain=domain.value,
        country=country,
        state=state.value,
        revision=1,
        updated_by=actor,
        created_at=current,
        updated_at=current,
    )
    session.add(source)
    await session.flush()
    return source


async def get_source(session: AsyncSession, source_id: str) -> Source | None:
    """За canonical `source_id` (не за UUID)."""
    return (
        await session.execute(select(Source).where(Source.source_id == source_id))
    ).scalar_one_or_none()


async def set_source_state(
    session: AsyncSession,
    source_pk: UUID,
    state: SourceState,
    *,
    expected_revision: int,
    reason: str | None,
    actor: str,
    now: datetime | None = None,
) -> Source:
    """Зміна `state` з optimistic revision; невідповідність → `StaleRevisionError`."""
    current = resolve_now(now)
    source = await session.scalar(
        update(Source)
        .where(Source.id == source_pk, Source.revision == expected_revision)
        .values(
            state=state.value,
            state_reason=reason,
            revision=Source.revision + 1,
            updated_by=actor,
            updated_at=current,
        )
        .returning(Source)
    )
    if source is None:
        await _raise_stale_or_missing(session, source_pk, expected_revision)
    return source


async def add_policy_version(
    session: AsyncSession,
    source_pk: UUID,
    policy: PolicySnapshot,
    *,
    expected_revision: int,
    actor: str | None = None,
    effective_from: datetime | None = None,
    now: datetime | None = None,
) -> SourcePolicyVersion:
    """Нова immutable версія policy (`version = max+1`) і `sources.current_policy_version_id`
    в одній транзакції; revision джерела перевіряється і збільшується."""
    current = resolve_now(now)
    source = await session.scalar(
        select(Source)
        .where(Source.id == source_pk, Source.revision == expected_revision)
        .with_for_update()
    )
    if source is None:
        await _raise_stale_or_missing(session, source_pk, expected_revision)
    last_version = await session.scalar(
        select(SourcePolicyVersion.version)
        .where(SourcePolicyVersion.source_id == source_pk)
        .order_by(SourcePolicyVersion.version.desc())
        .limit(1)
    )
    version = SourcePolicyVersion(
        id=new_entity_id(),
        source_id=source_pk,
        version=(last_version or 0) + 1,
        requests_per_second=policy.requests_per_second,
        max_concurrency=policy.max_concurrency,
        burst_tokens=policy.burst_tokens,
        crawl_interval_seconds=policy.crawl_interval_seconds,
        browser_allowed=policy.browser_allowed,
        robots_policy=policy.robots_policy,
        manifest_sha256=policy.manifest_sha256,
        manifest_uri=policy.manifest_uri,
        effective_from=effective_from or current,
        created_by=actor,
        created_at=current,
    )
    session.add(version)
    await session.flush()
    source.current_policy_version_id = version.id
    source.revision += 1
    source.updated_by = actor
    source.updated_at = current
    await session.flush()
    return version


async def upsert_route(
    session: AsyncSession,
    source_pk: UUID,
    route_kind: str,
    route_key: str,
    *,
    now: datetime | None = None,
) -> SourceRoute:
    """Створює route (`healthy`) або повертає існуючий за `(source_id, route_kind, route_key)`."""
    if route_kind not in ROUTE_KINDS:
        msg = f"невідомий route_kind {route_kind!r}; дозволені {ROUTE_KINDS}"
        raise ValueError(msg)
    current = resolve_now(now)
    stmt = (
        pg_insert(SourceRoute)
        .values(
            id=new_entity_id(),
            source_id=source_pk,
            route_kind=route_kind,
            route_key=route_key,
            state=RouteState.HEALTHY.value,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing(
            index_elements=[SourceRoute.source_id, SourceRoute.route_kind, SourceRoute.route_key]
        )
        .returning(SourceRoute)
    )
    route = (await session.execute(stmt)).scalar_one_or_none()
    if route is not None:
        return route
    existing = await session.scalar(
        select(SourceRoute)
        .where(
            SourceRoute.source_id == source_pk,
            SourceRoute.route_kind == route_kind,
            SourceRoute.route_key == route_key,
        )
        .execution_options(populate_existing=True)
    )
    if existing is None:  # pragma: no cover
        msg = "route зник між INSERT і SELECT"
        raise NotFoundError(msg)
    return existing


async def set_route_state(
    session: AsyncSession,
    route_id: UUID,
    state: RouteState,
    *,
    expected_revision: int,
    reason: str | None = None,
    circuit_open_until: datetime | None = None,
    now: datetime | None = None,
) -> SourceRoute:
    """Зміна стану route (circuit breaker) з optimistic revision."""
    current = resolve_now(now)
    route = await session.scalar(
        update(SourceRoute)
        .where(SourceRoute.id == route_id, SourceRoute.revision == expected_revision)
        .values(
            state=state.value,
            state_reason=reason,
            circuit_open_until=circuit_open_until,
            revision=SourceRoute.revision + 1,
            updated_at=current,
        )
        .returning(SourceRoute)
    )
    if route is None:
        exists = await session.scalar(
            select(SourceRoute.revision).where(SourceRoute.id == route_id)
        )
        if exists is None:
            msg = f"route {route_id} не знайдено"
            raise NotFoundError(msg)
        msg = f"route {route_id}: revision {expected_revision} застаріла (поточна {exists})"
        raise StaleRevisionError(msg)
    return route


async def upsert_cursor(
    session: AsyncSession,
    source_pk: UUID,
    cursor_kind: str,
    cursor_key: str,
    cursor_value: str,
    *,
    route_id: UUID | None = None,
    cursor_at: datetime | None = None,
    now: datetime | None = None,
) -> SourceCursor:
    """Вставляє або оновлює cursor (`revision + 1` при кожному оновленні)."""
    current = resolve_now(now)
    stmt = (
        pg_insert(SourceCursor)
        .values(
            id=new_entity_id(),
            source_id=source_pk,
            route_id=route_id,
            cursor_kind=cursor_kind,
            cursor_key=cursor_key,
            cursor_value=cursor_value,
            cursor_at=cursor_at,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_update(
            index_elements=[
                SourceCursor.source_id,
                SourceCursor.cursor_kind,
                SourceCursor.cursor_key,
            ],
            set_={
                "cursor_value": cursor_value,
                "cursor_at": cursor_at,
                "route_id": route_id,
                "revision": SourceCursor.revision + 1,
                "updated_at": current,
            },
        )
        .returning(SourceCursor)
        # Рядок міг уже бути в identity map (той самий session) — оновити атрибути з RETURNING.
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def _raise_stale_or_missing(
    session: AsyncSession, source_pk: UUID, expected_revision: int
) -> NoReturn:
    actual = await session.scalar(select(Source.revision).where(Source.id == source_pk))
    if actual is None:
        msg = f"source {source_pk} не знайдено"
        raise NotFoundError(msg)
    msg = f"source {source_pk}: revision {expected_revision} застаріла (поточна {actual})"
    raise StaleRevisionError(msg)
