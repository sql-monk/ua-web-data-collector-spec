"""Sources (optimistic revision, policy versions, routes, cursors) і crawl runs (FR-002)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts.enums import DataDomain, RouteState, SourceState
from collector.persistence.postgres.errors import (
    ConflictError,
    InvalidTransitionError,
    StaleRevisionError,
)
from collector.persistence.postgres.models import Source
from collector.persistence.postgres.repositories import crawl_runs, sources

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
AUDIT = {"actor": "op", "reason": "test"}
POLICY = sources.PolicySnapshot(
    requests_per_second=Decimal("0.5"),
    max_concurrency=2,
    crawl_interval_seconds=3600,
    robots_policy="respect",
    manifest_sha256="a" * 64,
)


async def _source(session: AsyncSession) -> Source:
    async with session.begin():
        return await sources.create_source(
            session, source_id="news_ua_example", domain=DataDomain.NEWS, country="UA", now=T0
        )


async def test_source_state_uses_optimistic_revision(pg_session: AsyncSession) -> None:
    source = await _source(pg_session)
    assert (source.state, source.revision) == ("paused", 1)
    async with pg_session.begin():
        enabled = await sources.set_source_state(
            pg_session,
            source.id,
            SourceState.ENABLED,
            expected_revision=1,
            reason="launch",
            actor="op",
            now=T0,
        )
    assert (enabled.state, enabled.revision) == ("enabled", 2)
    async with pg_session.begin():
        with pytest.raises(StaleRevisionError):
            await sources.set_source_state(
                pg_session,
                source.id,
                SourceState.DISABLED,
                expected_revision=1,
                reason="old",
                actor="op",
                now=T0,
            )
    async with pg_session.begin():
        found = await sources.get_source(pg_session, "news_ua_example")
    assert found is not None and found.state == "enabled"


async def test_policy_versions_are_immutable_and_sequential(pg_session: AsyncSession) -> None:
    source = await _source(pg_session)
    async with pg_session.begin():
        v1 = await sources.add_policy_version(
            pg_session, source.id, POLICY, expected_revision=1, now=T0, **AUDIT
        )
        v2 = await sources.add_policy_version(
            pg_session, source.id, POLICY, expected_revision=2, now=T0, **AUDIT
        )
    assert (v1.version, v2.version) == (1, 2)
    async with pg_session.begin():
        refreshed = await sources.get_source(pg_session, "news_ua_example")
    assert refreshed is not None
    assert refreshed.current_policy_version_id == v2.id
    assert refreshed.revision == 3
    async with pg_session.begin():
        with pytest.raises(StaleRevisionError):
            await sources.add_policy_version(
                pg_session, source.id, POLICY, expected_revision=1, now=T0, **AUDIT
            )


async def test_routes_and_cursors_upsert(pg_session: AsyncSession) -> None:
    source = await _source(pg_session)
    async with pg_session.begin():
        route = await sources.upsert_route(
            pg_session, source.id, "rss", "https://x.test/rss", now=T0, **AUDIT
        )
        same = await sources.upsert_route(
            pg_session, source.id, "rss", "https://x.test/rss", now=T0, **AUDIT
        )
        assert same.id == route.id
        opened = await sources.set_route_state(
            pg_session,
            route.id,
            RouteState.CIRCUIT_OPEN,
            expected_revision=1,
            actor="fetcher",
            reason="5xx",
            circuit_open_until=T0,
            now=T0,
        )
        assert (opened.state, opened.revision) == ("circuit_open", 2)
        with pytest.raises(StaleRevisionError):
            await sources.set_route_state(
                pg_session, route.id, RouteState.HEALTHY, expected_revision=1, now=T0, **AUDIT
            )
        with pytest.raises(ValueError, match="route_kind"):
            await sources.upsert_route(pg_session, source.id, "ftp", "x", now=T0, **AUDIT)
        c1 = await sources.upsert_cursor(
            pg_session,
            source.id,
            "rss",
            "https://x.test/rss",
            "etag-1",
            route_id=route.id,
            now=T0,
            **AUDIT,
        )
        c2 = await sources.upsert_cursor(
            pg_session,
            source.id,
            "rss",
            "https://x.test/rss",
            "etag-2",
            route_id=route.id,
            now=T0,
            **AUDIT,
        )
    assert c1.id == c2.id
    assert (c2.cursor_value, c2.revision) == ("etag-2", 2)


async def test_only_one_running_full_crawl_run_per_source(pg_session: AsyncSession) -> None:
    source = await _source(pg_session)
    async with pg_session.begin():
        full = await crawl_runs.start_run(
            pg_session, source.id, "full", started_by="scheduler", now=T0
        )
        incremental = await crawl_runs.start_run(
            pg_session, source.id, "incremental", started_by="scheduler", now=T0
        )
        assert full.id != incremental.id
        with pytest.raises(ConflictError):
            await crawl_runs.start_run(
                pg_session, source.id, "full", started_by="scheduler", now=T0
            )
        # Транзакція не зіпсована (ON CONFLICT, не IntegrityError) — можна продовжувати.
        finished = await crawl_runs.finish_run(pg_session, full.id, "succeeded", now=T0)
        assert finished.status == "succeeded"
        second_full = await crawl_runs.start_run(
            pg_session, source.id, "full", started_by="scheduler", now=T0
        )
        assert second_full.status == "running"
        with pytest.raises(InvalidTransitionError):
            await crawl_runs.finish_run(pg_session, full.id, "failed", now=T0)
        with pytest.raises(ValueError, match="kind"):
            await crawl_runs.start_run(pg_session, source.id, "weird", started_by="x", now=T0)
