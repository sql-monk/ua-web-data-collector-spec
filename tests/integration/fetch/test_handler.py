"""FetchHandler persistence, preflight and replay semantics against PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts import new_entity_id
from collector.contracts.enums import (
    ContentAccess,
    DataDomain,
    FetchOutcome,
    RouteState,
    SourceState,
)
from collector.fetch.classify import FetchDecision
from collector.fetch.client import FetchRequest, FetchResult, SafeFetcher
from collector.fetch.handler import FetchHandler
from collector.persistence.postgres.models import CrawlJob, Fetch, RawObject, SourceRoute
from collector.persistence.postgres.repositories import artifacts, sources
from collector.storage import ArtifactStore, ClaimedUploader
from collector.storage.testing import FakeArtifactStore
from collector.workers.handlers import HandlerContext, Task
from collector.workers.roles import WorkerRole

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
URL = "https://news.example.test/article/1"
POLICY = sources.PolicySnapshot(
    requests_per_second=Decimal("1"),
    max_concurrency=1,
    crawl_interval_seconds=3600,
    robots_policy="diagnostic",
    manifest_sha256="b" * 64,
    browser_allowed=True,
)


class StubFetcher:
    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.requests: list[FetchRequest] = []

    async def fetch(self, request: FetchRequest) -> FetchResult:
        self.requests.append(request)
        return self.result


class SequenceFetcher(StubFetcher):
    def __init__(self, *results: FetchResult) -> None:
        super().__init__(results[-1])
        self.results = list(results)

    async def fetch(self, request: FetchRequest) -> FetchResult:
        self.requests.append(request)
        return self.results.pop(0)


async def seed(
    sessions: async_sessionmaker[AsyncSession],
    *,
    state: SourceState = SourceState.ENABLED,
    policy: sources.PolicySnapshot = POLICY,
) -> tuple[object, object]:
    async with sessions() as session, session.begin():
        source = await sources.create_source(
            session,
            source_id="news_ua_example",
            domain=DataDomain.NEWS,
            country="UA",
            state=state,
            now=T0,
        )
        await sources.add_policy_version(
            session,
            source.id,
            policy,
            expected_revision=source.revision,
            actor="test",
            reason="seed",
            now=T0,
        )
        route = await sources.upsert_route(
            session, source.id, "detail", URL, actor="test", reason="seed", now=T0
        )
    return source, route


def build_handler(
    sessions: async_sessionmaker[AsyncSession],
    fetcher: StubFetcher,
    *,
    robots_ttl: timedelta = timedelta(hours=24),
) -> tuple[FetchHandler, FakeArtifactStore]:
    store = FakeArtifactStore(buckets={"raw"})
    context = HandlerContext(
        role=WorkerRole.FETCH,
        sessions=sessions,
        worker_instance_id=new_entity_id(),
        clock=cast(Callable[[], datetime], lambda: T0),
        env={},
    )
    uploader = ClaimedUploader(sessions, store, owner=context.owner, clock=context.clock)
    return (
        FetchHandler(
            context,
            cast(SafeFetcher, fetcher),
            uploader,
            cast(ArtifactStore, store),
            robots_ttl=robots_ttl,
            user_agent="UAWebDataCollector/test",
        ),
        store,
    )


def task(source_id: object, route_id: object) -> Task:
    return Task(
        job_id=new_entity_id(),
        job_type="fetch.http",
        args={"url": URL, "route_id": str(route_id)},
        attempt=1,
        max_attempts=4,
        priority=100,
        not_before=T0,
        run_id=None,
        source_id=source_id,  # type: ignore[arg-type]
    )


async def test_robots_job_snapshots_once_within_ttl_and_never_enqueues_parse(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions)
    robots_body = b"User-agent: *\nDisallow: /private\n"
    fetcher = StubFetcher(
        FetchResult(
            FetchDecision(FetchOutcome.SUCCESS, ContentAccess.FULL),
            "https://news.example.test/robots.txt",
            final_url="https://news.example.test/robots.txt",
            status=200,
            body=robots_body,
            decoded_bytes=len(robots_body),
            media_type="text/plain",
        )
    )
    handler, store = build_handler(pg_sessions, fetcher)
    robots_task = task(source.id, route.id)  # type: ignore[attr-defined]
    robots_task = Task(
        job_id=robots_task.job_id,
        job_type=robots_task.job_type,
        args={**robots_task.args, "request_kind": "robots"},
        attempt=robots_task.attempt,
        max_attempts=robots_task.max_attempts,
        priority=robots_task.priority,
        not_before=robots_task.not_before,
        run_id=robots_task.run_id,
        source_id=robots_task.source_id,
    )

    assert (await handler.handle(robots_task)).disposition == "complete"
    assert (await handler.handle(robots_task)).disposition == "complete"
    assert len(fetcher.requests) == 1
    assert fetcher.requests[0].url == "https://news.example.test/robots.txt"
    assert len(store.put_calls) == 1
    async with pg_sessions() as session:
        fetch = (await session.execute(select(Fetch))).scalar_one()
        assert fetch.request_variant == "robots"
        assert await session.scalar(select(func.count()).select_from(CrawlJob)) == 0


async def test_respect_policy_snapshots_robots_then_blocks_page_without_request(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions, policy=replace(POLICY, robots_policy="respect"))
    robots_body = b"User-agent: *\nDisallow: /article/\n"
    fetcher = SequenceFetcher(
        FetchResult(
            FetchDecision(FetchOutcome.SUCCESS, ContentAccess.FULL),
            "https://news.example.test/robots.txt",
            final_url="https://news.example.test/robots.txt",
            status=200,
            body=robots_body,
            decoded_bytes=len(robots_body),
            media_type="text/plain",
        ),
        FetchResult(
            FetchDecision(FetchOutcome.SUCCESS, ContentAccess.FULL),
            URL,
            final_url=URL,
            status=200,
            body=b"must not be requested",
        ),
    )
    handler, _ = build_handler(pg_sessions, fetcher)

    result = await handler.handle(task(source.id, route.id))  # type: ignore[attr-defined]

    assert result.disposition == "quarantine"
    assert result.error_code == "policy_blocked"
    assert [request.request_kind for request in fetcher.requests] == ["robots"]
    async with pg_sessions() as session:
        rows = (await session.execute(select(Fetch).order_by(Fetch.created_at))).scalars().all()
        assert len(rows) == 2
        assert rows[0].request_variant == "robots"
        assert rows[0].raw_sha256 is not None
        assert rows[1].error_code == "policy_blocked"
        assert await session.scalar(select(func.count()).select_from(CrawlJob)) == 0


async def test_respect_policy_does_not_allow_page_when_robots_is_forbidden(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions, policy=replace(POLICY, robots_policy="respect"))
    fetcher = StubFetcher(
        FetchResult(
            FetchDecision(
                FetchOutcome.PERMANENT_FAILURE,
                ContentAccess.BLOCKED,
                "http_403",
                route_incident=True,
            ),
            "https://news.example.test/robots.txt",
            final_url="https://news.example.test/robots.txt",
            status=403,
        )
    )
    handler, _ = build_handler(pg_sessions, fetcher)

    result = await handler.handle(task(source.id, route.id))  # type: ignore[attr-defined]

    assert (result.disposition, result.error_code) == ("quarantine", "http_403")
    assert [request.request_kind for request in fetcher.requests] == ["robots"]
    async with pg_sessions() as session:
        [recorded] = (await session.execute(select(Fetch))).scalars().all()
        assert recorded.request_variant == "robots"
        assert recorded.http_status == 403
        route_state = await session.scalar(
            select(SourceRoute.state).where(SourceRoute.id == route.id)  # type: ignore[attr-defined]
        )
        assert route_state == RouteState.HEALTHY.value


async def test_success_uploads_raw_and_atomically_enqueues_parse(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions)
    body = b"<html>article</html>"
    fetcher = StubFetcher(
        FetchResult(
            decision=FetchDecision(FetchOutcome.SUCCESS, ContentAccess.FULL),
            requested_url=URL,
            final_url=URL,
            status=200,
            headers={"content-type": "text/html", "etag": '"v1"'},
            body=body,
            decoded_bytes=len(body),
            media_type="text/html",
        )
    )
    handler, store = build_handler(pg_sessions, fetcher)

    result = await handler.handle(task(source.id, route.id))  # type: ignore[attr-defined]
    assert result.disposition == "complete"
    assert len(store.put_calls) == 1
    async with pg_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Fetch)) == 1
        raw = (await session.execute(select(RawObject))).scalar_one()
        parse = (
            await session.execute(select(CrawlJob).where(CrawlJob.job_type == "parse.raw"))
        ).scalar_one()
        assert parse.args["fetch_id"] == str(raw.first_fetch_id)
        assert parse.args["raw_sha256"] == raw.sha256


async def test_paused_source_defers_without_http_or_persistence(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions, state=SourceState.PAUSED)
    fetcher = StubFetcher(FetchResult(FetchDecision(FetchOutcome.SUCCESS, ContentAccess.FULL), URL))
    handler, _ = build_handler(pg_sessions, fetcher)

    result = await handler.handle(task(source.id, route.id))  # type: ignore[attr-defined]
    assert result.disposition == "defer"
    assert result.error_code == "source_not_enabled"
    assert fetcher.requests == []
    async with pg_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Fetch)) == 0


async def test_403_records_fetch_opens_route_and_browser_enqueue_is_idempotent(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions)
    fetcher = StubFetcher(
        FetchResult(
            FetchDecision(
                FetchOutcome.PERMANENT_FAILURE,
                ContentAccess.BLOCKED,
                "http_403",
                route_incident=True,
                browser_candidate=True,
            ),
            URL,
            final_url=URL,
            status=403,
        )
    )
    handler, _ = build_handler(pg_sessions, fetcher)
    fetch_task = task(source.id, route.id)  # type: ignore[attr-defined]

    result = await handler.handle(fetch_task)
    assert result.disposition == "quarantine"
    replay = await handler.handle(fetch_task)
    assert replay.disposition == "defer"
    assert len(fetcher.requests) == 1
    async with pg_sessions() as session:
        route_state = await session.scalar(
            select(SourceRoute.state).where(SourceRoute.id == route.id)  # type: ignore[attr-defined]
        )
        browser_jobs = await session.scalar(
            select(func.count()).select_from(CrawlJob).where(CrawlJob.job_type == "browser.render")
        )
        assert route_state == RouteState.CIRCUIT_OPEN.value
        assert browser_jobs == 1


async def test_304_uses_latest_validators_and_records_no_raw_object(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions)
    async with pg_sessions() as session, session.begin():
        await artifacts.record_fetch(
            session,
            artifacts.FetchRecord(
                requested_url=URL,
                outcome=FetchOutcome.SUCCESS,
                fetched_at=T0,
                source_id=source.id,  # type: ignore[attr-defined]
                http_status=200,
                content_access=ContentAccess.FULL,
                etag='"cached"',
                last_modified_raw="Wed, 23 Sep 2026 10:00:00 GMT",
                raw_sha256="a" * 64,
            ),
            now=T0,
        )
    fetcher = StubFetcher(
        FetchResult(
            FetchDecision(
                FetchOutcome.SUCCESS,
                ContentAccess.UNKNOWN,
                not_modified=True,
            ),
            URL,
            final_url=URL,
            status=304,
        )
    )
    handler, store = build_handler(pg_sessions, fetcher)

    result = await handler.handle(task(source.id, route.id))  # type: ignore[attr-defined]
    assert result.disposition == "complete"
    assert fetcher.requests[0].if_none_match == '"cached"'
    assert fetcher.requests[0].if_modified_since == "Wed, 23 Sep 2026 10:00:00 GMT"
    assert store.put_calls == []
    async with pg_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Fetch)) == 2
        assert await session.scalar(select(func.count()).select_from(RawObject)) == 0


async def test_429_records_attempt_and_returns_retry_after_lower_bound(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source, route = await seed(pg_sessions)
    fetcher = StubFetcher(
        FetchResult(
            FetchDecision(
                FetchOutcome.RETRYABLE,
                ContentAccess.UNKNOWN,
                "http_429",
                retry_after=timedelta(minutes=7),
                block_origin=True,
            ),
            URL,
            final_url=URL,
            status=429,
        )
    )
    handler, _ = build_handler(pg_sessions, fetcher)

    result = await handler.handle(task(source.id, route.id))  # type: ignore[attr-defined]
    assert result.disposition == "retry"
    assert result.not_before == T0 + timedelta(minutes=7)
    async with pg_sessions() as session:
        recorded = (await session.execute(select(Fetch))).scalar_one()
        assert recorded.http_status == 429
        assert recorded.raw_sha256 is None
