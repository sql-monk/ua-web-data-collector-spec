"""PR3a п.4–6, п.8: читання fetch-а перед запитом і після нього (WP-02 п.1–4).

- `get_fetch_preflight` — стан джерела, чинна policy і route одним запитом; `None` для
  відсутнього/чужого;
- `latest_validators` — ETag/Last-Modified останнього 200/206 (304 не оновлює); EXPLAIN —
  index scan `ix_fetches_validators` у партиціях, без seq scan;
- `record_route_failure`/`reset_route_failures` — атомарний лічильник, `circuit_open` + audit
  на порозі, конкурентні інкременти не губляться;
- `count_retries_since` — `retryable` fetches джерела за вікно.

Під LOGIN-роллю `collector_fetcher` ці операції перевіряє `test_pr3a_roles.py`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts import new_entity_id
from collector.contracts.enums import DataDomain, FetchOutcome, RouteState, SourceState
from collector.persistence.postgres.errors import InvalidValueError, NotFoundError
from collector.persistence.postgres.models import AuditLog, Fetch, Source, SourceRoute
from collector.persistence.postgres.repositories import artifacts, sources

from .conftest import FIXED_NOW

pytestmark = pytest.mark.integration

T0 = FIXED_NOW
URL = "https://news.example.test/article/1"
POLICY = sources.PolicySnapshot(
    requests_per_second=Decimal("0.2"),
    max_concurrency=1,
    crawl_interval_seconds=3600,
    robots_policy="respect",
    manifest_sha256="b" * 64,
    browser_allowed=True,
)


async def seed_source(
    session: AsyncSession,
    *,
    source_id: str = "news_ua_example",
    state: SourceState = SourceState.ENABLED,
    with_policy: bool = True,
) -> tuple[Source, SourceRoute]:
    """Джерело + (опційно) policy + route `rss` — через репозиторії з audit."""
    async with session.begin():
        source = await sources.create_source(
            session, source_id=source_id, domain=DataDomain.NEWS, country="UA", state=state, now=T0
        )
        if with_policy:
            await sources.add_policy_version(
                session,
                source.id,
                POLICY,
                expected_revision=source.revision,
                actor="test",
                reason="seed",
                now=T0,
            )
        route = await sources.upsert_route(
            session, source.id, "rss", "https://news.example.test/rss", actor="test", reason="seed"
        )
    return source, route


async def test_count_retries_since_rejects_naive_boundary_before_sql(
    pg_session: AsyncSession,
) -> None:
    source, _ = await seed_source(pg_session)
    naive = datetime(2026, 9, 24, 12)  # noqa: DTZ001 — contract under test
    with pytest.raises(ValueError, match="since.*aware"):
        await artifacts.count_retries_since(pg_session, source.id, naive)


# --- preflight ---------------------------------------------------------------------------


async def test_preflight_returns_state_current_policy_and_route(pg_session: AsyncSession) -> None:
    source, route = await seed_source(pg_session)
    async with pg_session.begin():
        preflight = await sources.get_fetch_preflight(pg_session, source.id, route.id)
    assert preflight is not None
    assert (preflight.source_state, preflight.route_state) == (
        SourceState.ENABLED,
        RouteState.HEALTHY,
    )
    assert (preflight.source_id, preflight.route_kind, preflight.policy_version) == (
        "news_ua_example",
        "rss",
        1,
    )
    assert preflight.policy == POLICY
    assert preflight.route_revision == route.revision


async def test_preflight_sees_newest_policy_paused_source_and_open_circuit(
    pg_session: AsyncSession,
) -> None:
    source, route = await seed_source(pg_session)
    async with pg_session.begin():
        current = await pg_session.get(Source, source.id, populate_existing=True)
        assert current is not None
        await sources.add_policy_version(
            pg_session,
            source.id,
            sources.PolicySnapshot(
                requests_per_second=Decimal("1"),
                max_concurrency=2,
                crawl_interval_seconds=600,
                robots_policy="respect",
                manifest_sha256="c" * 64,
            ),
            expected_revision=current.revision,
            actor="op",
            reason="new manifest",
            now=T0,
        )
        current = await pg_session.get(Source, source.id, populate_existing=True)
        assert current is not None
        await sources.set_source_state(
            pg_session,
            source.id,
            SourceState.PAUSED,
            expected_revision=current.revision,
            actor="op",
            reason="maintenance",
            now=T0,
        )
        await sources.set_route_state(
            pg_session,
            route.id,
            RouteState.CIRCUIT_OPEN,
            expected_revision=route.revision,
            actor="op",
            reason="403 storm",
            circuit_open_until=T0 + timedelta(hours=1),
            now=T0,
        )
        preflight = await sources.get_fetch_preflight(pg_session, source.id, route.id)
    assert preflight is not None
    assert preflight.source_state is SourceState.PAUSED
    assert preflight.route_state is RouteState.CIRCUIT_OPEN
    assert preflight.circuit_open_until == T0 + timedelta(hours=1)
    assert preflight.policy_version == 2
    assert preflight.policy is not None and preflight.policy.manifest_sha256 == "c" * 64


async def test_preflight_is_none_for_missing_source_route_or_foreign_route(
    pg_session: AsyncSession,
) -> None:
    source, route = await seed_source(pg_session)
    other, other_route = await seed_source(pg_session, source_id="news_ua_other")
    async with pg_session.begin():
        assert await sources.get_fetch_preflight(pg_session, new_entity_id(), route.id) is None
        assert await sources.get_fetch_preflight(pg_session, source.id, new_entity_id()) is None
        assert await sources.get_fetch_preflight(pg_session, source.id, other_route.id) is None
        assert await sources.get_fetch_preflight(pg_session, other.id, other_route.id) is not None


async def test_preflight_without_policy_version_reports_none(pg_session: AsyncSession) -> None:
    source, route = await seed_source(pg_session, with_policy=False)
    async with pg_session.begin():
        preflight = await sources.get_fetch_preflight(pg_session, source.id, route.id)
    assert preflight is not None
    assert (preflight.policy, preflight.policy_version) == (None, None)


# --- validators --------------------------------------------------------------------------


async def _fetch(
    session: AsyncSession,
    source_id: UUID,
    at: datetime,
    *,
    status: int | None,
    outcome: FetchOutcome = FetchOutcome.SUCCESS,
    etag: str | None = None,
    last_modified: str | None = None,
    url: str = URL,
) -> None:
    async with session.begin():
        await artifacts.record_fetch(
            session,
            artifacts.FetchRecord(
                requested_url=url,
                outcome=outcome,
                fetched_at=at,
                source_id=source_id,
                http_status=status,
                etag=etag,
                last_modified_raw=last_modified,
            ),
            now=at,
        )


async def test_validators_follow_last_body_response_and_ignore_304(
    pg_session: AsyncSession,
) -> None:
    source, _ = await seed_source(pg_session)
    async with pg_session.begin():
        assert await artifacts.latest_validators(pg_session, source.id, URL) is None
    lm = "Tue, 22 Sep 2026 10:00:00 GMT"
    await _fetch(pg_session, source.id, T0, status=200, etag='"v1"', last_modified=lm)
    async with pg_session.begin():
        first = await artifacts.latest_validators(pg_session, source.id, URL)
    assert first == artifacts.Validators(etag='"v1"', last_modified=lm, fetched_at=T0)

    # Пізніший 304 (O-7: success, без тіла) validators не оновлює.
    await _fetch(pg_session, source.id, T0 + timedelta(hours=1), status=304)
    # Помилки й інший URL того самого джерела — теж ні.
    await _fetch(
        pg_session,
        source.id,
        T0 + timedelta(hours=2),
        status=503,
        outcome=FetchOutcome.RETRYABLE,
        etag='"err"',
    )
    await _fetch(
        pg_session, source.id, T0 + timedelta(hours=2), status=200, etag='"x"', url=URL + "x"
    )
    async with pg_session.begin():
        assert await artifacts.latest_validators(pg_session, source.id, URL) == first

    # Пізніший 200 з новим ETag → новий; наступний місяць — інша партиція.
    later = T0 + timedelta(days=31)
    await _fetch(pg_session, source.id, later, status=200, etag='"v2"')
    async with pg_session.begin():
        newest = await artifacts.latest_validators(pg_session, source.id, URL)
    assert newest == artifacts.Validators(etag='"v2"', last_modified=None, fetched_at=later)


async def test_validators_accept_206_and_are_scoped_to_source(pg_session: AsyncSession) -> None:
    source, _ = await seed_source(pg_session)
    other, _ = await seed_source(pg_session, source_id="news_ua_other")
    await _fetch(pg_session, source.id, T0, status=206, etag='"partial"')
    async with pg_session.begin():
        own = await artifacts.latest_validators(pg_session, source.id, URL)
        foreign = await artifacts.latest_validators(pg_session, other.id, URL)
    assert own is not None and own.etag == '"partial"'
    assert foreign is None


async def test_validators_query_uses_partial_index_without_seq_scan(
    pg_session: AsyncSession,
) -> None:
    source, _ = await seed_source(pg_session)
    for month in range(3):
        for n in range(20):
            await _fetch(
                pg_session,
                source.id,
                T0 + timedelta(days=31 * month, minutes=n),
                status=200,
                etag=f'"{month}-{n}"',
                url=f"{URL}?n={n}",
            )
    stmt = (
        select(Fetch.etag)
        .where(*artifacts.validators_predicate(source.id, URL + "?n=3"))
        .order_by(Fetch.fetched_at.desc())
        .limit(1)
    )
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    async with pg_session.begin():
        await pg_session.execute(text("ANALYZE fetches"))
        # Малі партиції планувальник і так читав би seq scan-ом; вимикаємо його, щоб довести,
        # що індекс ЗАСТОСОВНИЙ до цього предиката в кожній партиції.
        await pg_session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            (await pg_session.execute(text(f"EXPLAIN (COSTS OFF) {compiled}"))).scalars().all()
        )
    assert "Seq Scan" not in plan
    # Індекси партицій PostgreSQL іменує сам (`fetches_y2026m09_source_id_requested_url_…`):
    # перевіряємо, що кожна гілка Append — index scan за ключем `requested_url_md5`.
    scans = [line for line in plan.splitlines() if "Scan" in line]
    assert scans and all("Index Scan" in line for line in scans), plan
    assert all("_source_id_requested_url_md5_fetched_at_idx" in line for line in scans), plan
    conditions = [line for line in plan.splitlines() if "Index Cond" in line]
    assert len(conditions) == len(scans), plan
    assert all("requested_url_md5" in line for line in conditions), plan
    assert "Sort Key: fetches.fetched_at DESC" in plan or len(scans) == 1, plan


# --- route failures ----------------------------------------------------------------------


async def _audit_actions(session: AsyncSession, resource_id: str) -> list[str]:
    rows = await session.execute(
        select(AuditLog.action)
        .where(AuditLog.resource_id == resource_id)
        .order_by(AuditLog.created_at)
    )
    return list(rows.scalars().all())


async def test_route_failure_threshold_opens_circuit_with_audit_and_reset_clears(
    pg_session: AsyncSession,
) -> None:
    _, route = await seed_source(pg_session)
    initial_revision = route.revision
    states = []
    for n in range(1, 4):
        async with pg_session.begin():
            states.append(
                await sources.record_route_failure(
                    pg_session,
                    route.id,
                    actor="fetch-worker",
                    reason="http_503",
                    threshold=3,
                    circuit_open_for=timedelta(minutes=30),
                    now=T0 + timedelta(minutes=n),
                )
            )
    assert states == [RouteState.HEALTHY, RouteState.HEALTHY, RouteState.CIRCUIT_OPEN]
    async with pg_session.begin():
        row = await pg_session.get(SourceRoute, route.id, populate_existing=True)
        assert row is not None
        assert (row.consecutive_failures, row.state) == (3, "circuit_open")
        assert row.circuit_open_until == T0 + timedelta(minutes=33)
        assert row.revision == initial_revision + 1
        assert row.last_failure_at == T0 + timedelta(minutes=3)
        actions = await _audit_actions(pg_session, str(route.id))
    assert actions.count("source_route.circuit_open") == 1

    # Четвертий збій: circuit уже відкритий — повторного переходу/audit немає.
    async with pg_session.begin():
        state = await sources.record_route_failure(
            pg_session, route.id, actor="fetch-worker", reason="http_503", threshold=3, now=T0
        )
        assert state is RouteState.CIRCUIT_OPEN
        assert (await _audit_actions(pg_session, str(route.id))).count(
            "source_route.circuit_open"
        ) == 1
        reset = await sources.reset_route_failures(
            pg_session, route.id, now=T0 + timedelta(hours=1)
        )
        row = await pg_session.get(SourceRoute, route.id, populate_existing=True)
    assert reset is RouteState.CIRCUIT_OPEN, "успіх не закриває circuit — це рішення оператора"
    assert row is not None
    assert (row.consecutive_failures, row.last_success_at) == (0, T0 + timedelta(hours=1))


async def test_route_failure_does_not_override_unsupported_and_validates_input(
    pg_session: AsyncSession,
) -> None:
    _, route = await seed_source(pg_session)
    async with pg_session.begin():
        await sources.set_route_state(
            pg_session,
            route.id,
            RouteState.UNSUPPORTED,
            expected_revision=route.revision,
            actor="op",
            reason="login wall",
            now=T0,
        )
        assert (
            await sources.record_route_failure(
                pg_session, route.id, actor="w", reason="http_403", threshold=1, now=T0
            )
            is RouteState.UNSUPPORTED
        )
        with pytest.raises(NotFoundError):
            await sources.record_route_failure(
                pg_session, new_entity_id(), actor="w", reason="x", threshold=1, now=T0
            )
        with pytest.raises(NotFoundError):
            await sources.reset_route_failures(pg_session, new_entity_id(), now=T0)
        with pytest.raises(InvalidValueError):
            await sources.record_route_failure(
                pg_session, route.id, actor=" ", reason="x", threshold=1, now=T0
            )
        with pytest.raises(ValueError, match="threshold"):
            await sources.record_route_failure(
                pg_session, route.id, actor="w", reason="x", threshold=0, now=T0
            )
        for invalid_duration in (timedelta(0), timedelta(microseconds=-1)):
            with pytest.raises(ValueError, match="circuit_open_for"):
                await sources.record_route_failure(
                    pg_session,
                    route.id,
                    actor="w",
                    reason="x",
                    threshold=1,
                    circuit_open_for=invalid_duration,
                    now=T0,
                )


async def test_concurrent_route_failures_are_not_lost(
    pg_sessions: async_sessionmaker[AsyncSession], pg_session: AsyncSession
) -> None:
    _, route = await seed_source(pg_session)
    workers = 8
    per_worker = 5

    async def fail_many() -> list[RouteState]:
        results = []
        async with pg_sessions() as session:
            for _ in range(per_worker):
                async with session.begin():
                    results.append(
                        await sources.record_route_failure(
                            session, route.id, actor="w", reason="timeout", threshold=10, now=T0
                        )
                    )
        return results

    outcomes = await asyncio.gather(*(fail_many() for _ in range(workers)))
    async with pg_session.begin():
        row = await pg_session.get(SourceRoute, route.id, populate_existing=True)
        opened = await pg_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_id == str(route.id),
                AuditLog.action == "source_route.circuit_open",
            )
        )
    assert row is not None and row.consecutive_failures == workers * per_worker
    assert opened == 1, "поріг спрацьовує рівно один раз"
    flat = [state for batch in outcomes for state in batch]
    assert flat.count(RouteState.HEALTHY) == 9


# --- retry budget ------------------------------------------------------------------------


async def test_count_retries_since_counts_only_retryable_of_this_source_in_window(
    pg_session: AsyncSession,
) -> None:
    source, _ = await seed_source(pg_session)
    other, _ = await seed_source(pg_session, source_id="news_ua_other")
    day = T0 - timedelta(hours=24)
    for minutes in (10, 20, 30):
        await _fetch(
            pg_session,
            source.id,
            T0 - timedelta(minutes=minutes),
            status=503,
            outcome=FetchOutcome.RETRYABLE,
        )
    await _fetch(
        pg_session,
        source.id,
        day - timedelta(minutes=1),
        status=503,
        outcome=FetchOutcome.RETRYABLE,
    )
    await _fetch(pg_session, source.id, T0, status=404, outcome=FetchOutcome.PERMANENT_FAILURE)
    await _fetch(pg_session, source.id, T0, status=200)
    await _fetch(pg_session, other.id, T0, status=503, outcome=FetchOutcome.RETRYABLE)
    async with pg_session.begin():
        assert await artifacts.count_retries_since(pg_session, source.id, day) == 3
        assert await artifacts.count_retries_since(pg_session, other.id, day) == 1
        assert await artifacts.count_retries_since(pg_session, new_entity_id(), day) == 0
