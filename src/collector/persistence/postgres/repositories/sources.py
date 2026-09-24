"""Control plane джерел: sources (optimistic revision), policy versions, routes, cursors.

Transaction boundary: викликач. Усі UPDATE versioned-ресурсів приймають `expected_revision`
і кидають `StaleRevisionError`, якщо рядок змінено кимось іншим (§7.6 stale GUI action).

**Audit — усередині репозиторію** (§13; знахідка S-2 пострев'ю PR1). У PR1 запис у `audit_log`
лишався обов'язком викликача, тож будь-який новий виклик міг тихо змінити control plane без
сліду. Тепер кожна mutating-операція цього модуля пише `append_audit` **у тій самій
транзакції**, що й зміну (той самий патерн, що `pools.request_scale`), і вимагає
`actor`/`reason` як обов'язкові аргументи: дію без сліду неможливо навіть написати. Операції,
які нічого не змінили (`upsert_route` для наявного route), сліду не лишають — це не мутація.

Винятки без audit — **лічильник** збоїв route (`record_route_failure` до порогу,
`reset_route_failures`): це телеметрія fetch-а, а не рішення; audit пишеться лише тоді, коли
лічильник переводить route у `circuit_open` (PR3a п.6).

Hot path fetch-а (PR3a п.4): `get_fetch_preflight` — один SELECT стану джерела, чинної policy
і route за UUID перед кожним запитом.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import NoReturn
from uuid import UUID

from sqlalchemy import and_, select, update
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
from collector.persistence.postgres.repositories.audit import append_audit, require_audit_context


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


@dataclass(frozen=True, slots=True)
class FetchPreflight:
    """Що fetch-у треба знати перед запитом (PR3a п.4, WP-02 п.1): стан джерела, чинна policy
    і стан route. `policy is None` — у джерела ще немає жодної policy-версії (fetch має
    відмовити: без лімітів запит заборонений)."""

    source_pk: UUID
    source_id: str
    source_state: SourceState
    policy: PolicySnapshot | None
    policy_version: int | None
    route_id: UUID
    route_state: RouteState
    route_revision: int
    route_kind: str
    circuit_open_until: datetime | None


async def get_fetch_preflight(
    session: AsyncSession, source_uuid: UUID, route_id: UUID
) -> FetchPreflight | None:
    """Стан джерела + чинна policy (`sources.current_policy_version_id`) + route **одним**
    запитом (PR3a п.4).

    `None` — джерела з таким UUID немає, route немає або route належить іншому джерелу (для
    fetch-а всі три випадки означають «не виконувати»). Доступно `collector_fetcher` (SELECT
    на `sources`, `source_policy_versions`, `source_routes`). Transaction boundary: викликач;
    один SELECT без блокувань.
    """
    row = (
        await session.execute(
            select(Source, SourceRoute, SourcePolicyVersion)
            .join(
                SourceRoute,
                and_(SourceRoute.source_id == Source.id, SourceRoute.id == route_id),
            )
            .outerjoin(
                SourcePolicyVersion, SourcePolicyVersion.id == Source.current_policy_version_id
            )
            .where(Source.id == source_uuid)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        return None
    source, route, version = row._tuple()
    return FetchPreflight(
        source_pk=source.id,
        source_id=source.source_id,
        source_state=SourceState(source.state),
        policy=_policy_snapshot(version) if version is not None else None,
        policy_version=version.version if version is not None else None,
        route_id=route.id,
        route_state=RouteState(route.state),
        route_revision=route.revision,
        route_kind=route.route_kind,
        circuit_open_until=route.circuit_open_until,
    )


def _policy_snapshot(version: SourcePolicyVersion) -> PolicySnapshot:
    return PolicySnapshot(
        requests_per_second=version.requests_per_second,
        max_concurrency=version.max_concurrency,
        crawl_interval_seconds=version.crawl_interval_seconds,
        robots_policy=version.robots_policy,
        manifest_sha256=version.manifest_sha256,
        burst_tokens=version.burst_tokens,
        browser_allowed=version.browser_allowed,
        manifest_uri=version.manifest_uri,
    )


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
    require_audit_context(actor, reason)
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
    require_audit_context(actor, reason)
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
    require_audit_context(actor, reason)
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
    require_audit_context(actor, reason)
    current = resolve_now(now)
    before_state = await session.scalar(select(SourceRoute.state).where(SourceRoute.id == route_id))
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


CIRCUIT_OPENABLE_ROUTE_STATES: frozenset[str] = frozenset(
    {RouteState.HEALTHY.value, RouteState.DEGRADED.value}
)
"""Стани, з яких лічильник збоїв відкриває circuit. `unsupported` — сильніше рішення
оператора/адаптера, лічильник його не перезаписує; `circuit_open` уже відкритий."""


async def record_route_failure(
    session: AsyncSession,
    route_id: UUID,
    *,
    actor: str,
    reason: str,
    threshold: int,
    circuit_open_for: timedelta | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> RouteState:
    """Атомарно `consecutive_failures + 1` і `last_failure_at = now` (PR3a п.6, WP-02 п.3).

    Якщо лічильник досяг `threshold`, а route у `healthy`/`degraded`, у **тій самій**
    транзакції route переходить у `circuit_open` (`circuit_open_until = now +
    circuit_open_for`, `None` — до рішення оператора), `revision + 1`, і пишеться audit
    `source_route.circuit_open` з `actor`/`reason`. Повертає стан route після виклику.

    Інкремент — один `UPDATE … SET consecutive_failures = consecutive_failures + 1`: row lock
    серіалізує конкурентних fetcher-ів, жоден збій не губиться, а поріг спрацьовує рівно
    один раз (перехід відбувається лише з `healthy`/`degraded`). Сам інкремент `revision` не
    змінює: інакше кожен збій робив би застарілою відкриту в GUI форму route.

    `actor`/`reason` обов'язкові завжди — виклик, що може відкрити circuit, мусить уміти
    залишити слід. Відсутній route → `NotFoundError`. Transaction boundary: викликач
    (зазвичай разом із `record_fetch`). GRANT `collector_fetcher` — лише column UPDATE
    лічильника/стану route (`sql/roles.sql`).
    """
    require_audit_context(actor, reason)
    if threshold < 1:
        msg = "threshold має бути >= 1"
        raise ValueError(msg)
    current = resolve_now(now)
    counted = (
        await session.execute(
            update(SourceRoute)
            .where(SourceRoute.id == route_id)
            .values(
                consecutive_failures=SourceRoute.consecutive_failures + 1,
                last_failure_at=current,
                updated_at=current,
            )
            .returning(SourceRoute.consecutive_failures, SourceRoute.state, SourceRoute.revision)
        )
    ).one_or_none()
    if counted is None:
        msg = f"route {route_id} не знайдено"
        raise NotFoundError(msg)
    failures, state, revision = counted._tuple()
    if failures < threshold or state not in CIRCUIT_OPENABLE_ROUTE_STATES:
        return RouteState(state)
    open_until = current + circuit_open_for if circuit_open_for is not None else None
    await session.execute(
        update(SourceRoute)
        .where(SourceRoute.id == route_id)
        .values(
            state=RouteState.CIRCUIT_OPEN.value,
            state_reason=reason,
            circuit_open_until=open_until,
            revision=SourceRoute.revision + 1,
            updated_at=current,
        )
        .execution_options(synchronize_session=False)
    )
    await append_audit(
        session,
        actor=actor,
        action="source_route.circuit_open",
        resource_type="source_route",
        resource_id=str(route_id),
        before={"state": state, "revision": revision},
        after={
            "state": RouteState.CIRCUIT_OPEN.value,
            "revision": revision + 1,
            "consecutive_failures": failures,
            "threshold": threshold,
            "circuit_open_until": open_until.isoformat() if open_until is not None else None,
            "reason": reason,
        },
        request_id=request_id,
        now=current,
    )
    return RouteState.CIRCUIT_OPEN


async def reset_route_failures(
    session: AsyncSession, route_id: UUID, *, now: datetime | None = None
) -> RouteState:
    """Успішний fetch: `consecutive_failures = 0`, `last_success_at = now` (PR3a п.6).

    Стан route **не** змінюється: закрити відкритий circuit — окреме рішення
    (`set_route_state` з audit), а не побічний ефект одного успіху. Повертає поточний стан;
    відсутній route → `NotFoundError`. Transaction boundary: викликач; без audit (телеметрія).
    """
    current = resolve_now(now)
    state = await session.scalar(
        update(SourceRoute)
        .where(SourceRoute.id == route_id)
        .values(consecutive_failures=0, last_success_at=current, updated_at=current)
        .returning(SourceRoute.state)
        .execution_options(synchronize_session=False)
    )
    if state is None:
        msg = f"route {route_id} не знайдено"
        raise NotFoundError(msg)
    return RouteState(state)


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

    Gate 3 (CR-9): якщо `cursor_value`/`cursor_at`/`route_id` не змінилися, рядок **не
    оновлюється** (ні `revision`, ні `updated_at`) і audit не пишеться — немає мутації, немає
    сліду; повертається наявний cursor. Будь-яка справжня зміна — рівно один audit-рядок.

    Cursor визначає, з якого місця система продовжить обхід, тож його ручний або помилковий
    зсув — класична причина «мовчазної» втрати даних; before/after у журналі роблять таке
    видимим (§13). Discovery пише сюди щотіку, тому `reason` має бути машинним і коротким
    (`"discovery tick"`), а не вільним текстом.
    """
    require_audit_context(actor, reason)
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
            where=(
                SourceCursor.cursor_value.is_distinct_from(cursor_value)
                | SourceCursor.cursor_at.is_distinct_from(cursor_at)
                | SourceCursor.route_id.is_distinct_from(route_id)
            ),
        )
        .returning(SourceCursor)
        # Рядок міг уже бути в identity map (той самий session) — оновити атрибути з RETURNING.
        .execution_options(populate_existing=True)
    )
    cursor = (await session.execute(stmt)).scalar_one_or_none()
    if cursor is None:
        # ON CONFLICT … WHERE не спрацював: значення ті самі — повертаємо наявний без сліду.
        unchanged = await session.scalar(
            select(SourceCursor)
            .where(
                SourceCursor.source_id == source_pk,
                SourceCursor.cursor_kind == cursor_kind,
                SourceCursor.cursor_key == cursor_key,
            )
            .execution_options(populate_existing=True)
        )
        if unchanged is None:  # pragma: no cover — можливо лише поза READ COMMITTED
            msg = f"cursor {cursor_kind}/{cursor_key} зник між INSERT і SELECT"
            raise NotFoundError(msg)
        return unchanged
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
