"""Control plane джерел: sources (optimistic revision), policy versions, routes, cursors.

Transaction boundary: викликач. Усі UPDATE versioned-ресурсів приймають `expected_revision`
і кидають `StaleRevisionError`, якщо рядок змінено кимось іншим (§7.6 stale GUI action).

**Audit — усередині репозиторію** (§13; знахідка S-2 пострев'ю PR1). У PR1 запис у `audit_log`
лишався обов'язком викликача, тож будь-який новий виклик міг тихо змінити control plane без
сліду. Тепер кожна mutating-операція цього модуля пише `append_audit` **у тій самій
транзакції**, що й зміну (той самий патерн, що `pools.request_scale`), і вимагає
`actor`/`reason` як обов'язкові аргументи: дію без сліду неможливо навіть написати. Операції,
які нічого не змінили (`upsert_route` для наявного route), сліду не лишають — це не мутація.
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
from collector.persistence.postgres.repositories.audit import append_audit


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
    reason: str,
    actor: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> Source:
    """Зміна `state` з optimistic revision; невідповідність → `StaleRevisionError`.

    Пише `audit_log` (before/after `state`+`revision`) у тій самій транзакції; `actor`/`reason`
    обов'язкові — вимкнення джерела без причини у журналі неможливе (§13).
    """
    current = resolve_now(now)
    before_state = await session.scalar(select(Source.state).where(Source.id == source_pk))
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
    await append_audit(
        session,
        actor=actor,
        action="source.set_state",
        resource_type="source",
        resource_id=source.source_id,
        before={"state": before_state, "revision": expected_revision},
        after={"state": source.state, "revision": source.revision, "reason": reason},
        request_id=request_id,
        now=current,
    )
    return source


async def add_policy_version(
    session: AsyncSession,
    source_pk: UUID,
    policy: PolicySnapshot,
    *,
    expected_revision: int,
    actor: str,
    reason: str,
    effective_from: datetime | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> SourcePolicyVersion:
    """Нова immutable версія policy (`version = max+1`) і `sources.current_policy_version_id`
    в одній транзакції; revision джерела перевіряється і збільшується.

    Audit (§13) — у тій самій транзакції: policy керує лімітером і розкладом, тож зміна без
    сліду «хто і навіщо» недопустима.
    """
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
    previous_policy_version_id = source.current_policy_version_id
    source.current_policy_version_id = version.id
    source.revision += 1
    source.updated_by = actor
    source.updated_at = current
    await session.flush()
    await append_audit(
        session,
        actor=actor,
        action="source.add_policy_version",
        resource_type="source",
        resource_id=source.source_id,
        before={
            "current_policy_version_id": str(previous_policy_version_id)
            if previous_policy_version_id is not None
            else None,
            "revision": expected_revision,
        },
        after={
            "current_policy_version_id": str(version.id),
            "policy_version": version.version,
            "manifest_sha256": policy.manifest_sha256,
            "revision": source.revision,
            "reason": reason,
        },
        request_id=request_id,
        now=current,
    )
    return version


async def upsert_route(
    session: AsyncSession,
    source_pk: UUID,
    route_kind: str,
    route_key: str,
    *,
    actor: str,
    reason: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> SourceRoute:
    """Створює route (`healthy`) або повертає існуючий за `(source_id, route_kind, route_key)`.

    Audit пишеться **лише при створенні**: повторний виклик для наявного route нічого не
    змінює, а журнал, у якому кожен discovery-тік лишає «зміну», нечитабельний (§13 — слід
    mutating actions, не кожного звернення).
    """
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
        await append_audit(
            session,
            actor=actor,
            action="source_route.create",
            resource_type="source_route",
            resource_id=str(route.id),
            after={
                "route_kind": route_kind,
                "route_key": route_key,
                "state": route.state,
                "reason": reason,
            },
            request_id=request_id,
            now=current,
        )
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
    actor: str,
    reason: str,
    circuit_open_until: datetime | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> SourceRoute:
    """Зміна стану route (circuit breaker) з optimistic revision + audit у тій самій транзакції.

    `circuit_open` вимикає частину джерела — це рішення, яке оператор має бачити в журналі
    разом із причиною (§13), тому `actor`/`reason` обов'язкові.
    """
    current = resolve_now(now)
    before_state = await session.scalar(
        select(SourceRoute.state).where(SourceRoute.id == route_id)
    )
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
    await append_audit(
        session,
        actor=actor,
        action="source_route.set_state",
        resource_type="source_route",
        resource_id=str(route.id),
        before={"state": before_state, "revision": expected_revision},
        after={
            "state": route.state,
            "revision": route.revision,
            "circuit_open_until": circuit_open_until.isoformat()
            if circuit_open_until is not None
            else None,
            "reason": reason,
        },
        request_id=request_id,
        now=current,
    )
    return route


async def upsert_cursor(
    session: AsyncSession,
    source_pk: UUID,
    cursor_kind: str,
    cursor_key: str,
    cursor_value: str,
    *,
    actor: str,
    reason: str,
    route_id: UUID | None = None,
    cursor_at: datetime | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> SourceCursor:
    """Вставляє або оновлює cursor (`revision + 1` при кожному оновленні) + audit.

    Cursor визначає, з якого місця система продовжить обхід, тож його ручний або помилковий
    зсув — класична причина «мовчазної» втрати даних; before/after у журналі роблять таке
    видимим (§13). Discovery пише сюди щотіку, тому `reason` має бути машинним і коротким
    (`"discovery tick"`), а не вільним текстом.
    """
    current = resolve_now(now)
    before_value = await session.scalar(
        select(SourceCursor.cursor_value).where(
            SourceCursor.source_id == source_pk,
            SourceCursor.cursor_kind == cursor_kind,
            SourceCursor.cursor_key == cursor_key,
        )
    )
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
    cursor = (await session.execute(stmt)).scalar_one()
    if before_value != cursor_value:
        await append_audit(
            session,
            actor=actor,
            action="source_cursor.upsert",
            resource_type="source_cursor",
            resource_id=str(cursor.id),
            before={"cursor_value": before_value},
            after={
                "cursor_kind": cursor_kind,
                "cursor_key": cursor_key,
                "cursor_value": cursor_value,
                "revision": cursor.revision,
                "reason": reason,
            },
            request_id=request_id,
            now=current,
        )
    return cursor


async def _raise_stale_or_missing(
    session: AsyncSession, source_pk: UUID, expected_revision: int
) -> NoReturn:
    actual = await session.scalar(select(Source.revision).where(Source.id == source_pk))
    if actual is None:
        msg = f"source {source_pk} не знайдено"
        raise NotFoundError(msg)
    msg = f"source {source_pk}: revision {expected_revision} застаріла (поточна {actual})"
    raise StaleRevisionError(msg)
