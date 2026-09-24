"""`SafeFetcher` + `PgOriginPermits` проти PostgreSQL-лімітера WP-01A (R-53, O-1).

- 429 першого клієнта → `block_origin`; другий клієнт (інший `owner_instance`) отримує
  `Denied("blocked")` і **не** робить запиту — respx бачить рівно 1 виклик сумарно;
- permit видає `acquire_permit`, після fetch (успіх/cancel) `live_permit_count == 0`;
- release ідемпотентний; bucket відсутній → запиту немає.
HTTP — respx, DNS — `FakeResolver`; мережа лише loopback до PostgreSQL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fetch_fakes import PUBLIC_IP, FakeResolver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts.enums import FetchOutcome
from collector.fetch.client import FetchRequest, SafeFetcher
from collector.fetch.config import FetchConfig
from collector.fetch.permits import Denied, Permit, PgOriginPermits
from collector.persistence.postgres.models import AuditLog
from collector.persistence.postgres.repositories import limiter

pytestmark = pytest.mark.integration

ORIGIN = "https://example.org"
URL = "https://example.org/page"


async def _bucket(sessions: async_sessionmaker[AsyncSession], *, concurrency: int = 2) -> None:
    async with sessions() as session, session.begin():
        await limiter.ensure_bucket(
            session,
            ORIGIN,
            capacity_tokens=10,
            refill_per_second="10",
            max_concurrency=concurrency,
        )


def _fetcher(sessions: async_sessionmaker[AsyncSession], owner: str) -> SafeFetcher:
    return SafeFetcher(
        config=FetchConfig(user_agent="UAWebDataCollector/0.1.0-test"),
        permits=PgOriginPermits(sessions, owner_instance=owner),
        resolver=FakeResolver({"example.org": [PUBLIC_IP]}),
    )


async def _live(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        return await limiter.live_permit_count(session, ORIGIN)


async def test_429_from_one_replica_blocks_the_other_without_request(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _bucket(pg_sessions)
    first = _fetcher(pg_sessions, "fetch-replica-1")
    second = _fetcher(pg_sessions, "fetch-replica-2")

    with respx.mock:
        route = respx.get(URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "120"}, content=b"")
        )
        blocked = await first.fetch(FetchRequest(URL))
        denied = await second.fetch(FetchRequest(URL))

    assert route.call_count == 1
    assert blocked.decision.error_code == "http_429"
    assert denied.denied is not None and denied.denied.reason == "blocked"
    assert denied.decision.outcome is FetchOutcome.RETRYABLE
    async with pg_sessions() as session:
        bucket = await limiter.get_bucket(session, ORIGIN)
        assert bucket is not None and bucket.blocked_until is not None
        assert bucket.block_reason == "http_429"
        audit = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "origin.block"))
        ).all()
        assert [row.actor for row in audit] == ["fetch-replica-1"]
    assert await _live(pg_sessions) == 0


async def test_permit_is_issued_by_limiter_and_released_after_fetch(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _bucket(pg_sessions)
    seen_live: list[int] = []

    async def responder(request: httpx.Request) -> httpx.Response:
        seen_live.append(await _live(pg_sessions))
        return httpx.Response(200, content=b"ok")

    with respx.mock:
        respx.get(URL).mock(side_effect=responder)
        result = await _fetcher(pg_sessions, "fetch-replica-1").fetch(FetchRequest(URL))

    assert result.body == b"ok"
    assert seen_live == [1]  # під час запиту permit живий
    assert await _live(pg_sessions) == 0


async def test_concurrency_denial_means_no_request(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _bucket(pg_sessions, concurrency=1)
    other = PgOriginPermits(pg_sessions, owner_instance="fetch-replica-9")
    held = await other.acquire(ORIGIN, None)
    assert isinstance(held, Permit)

    with respx.mock(assert_all_called=False):
        route = respx.get(URL).mock(return_value=httpx.Response(200, content=b"x"))
        result = await _fetcher(pg_sessions, "fetch-replica-1").fetch(FetchRequest(URL))

    assert route.call_count == 0
    assert result.denied is not None and result.denied.reason == "concurrency"
    await other.release(held)


async def test_release_is_idempotent_and_owner_scoped(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _bucket(pg_sessions)
    owner = PgOriginPermits(pg_sessions, owner_instance="fetch-replica-1")
    stranger = PgOriginPermits(pg_sessions, owner_instance="fetch-replica-2")
    permit = await owner.acquire(ORIGIN, None)
    assert isinstance(permit, Permit)

    await stranger.release(permit)  # чужий owner не звільняє slot
    assert await _live(pg_sessions) == 1
    await owner.release(permit)
    await owner.release(permit)
    assert await _live(pg_sessions) == 0


async def test_unknown_origin_is_denied_without_request(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    permits = PgOriginPermits(pg_sessions, owner_instance="fetch-replica-1")
    assert await permits.acquire(ORIGIN, None) == Denied("unknown_origin")

    with respx.mock(assert_all_called=False):
        route = respx.get(URL).mock(return_value=httpx.Response(200, content=b"x"))
        result = await _fetcher(pg_sessions, "fetch-replica-1").fetch(FetchRequest(URL))

    assert route.call_count == 0
    assert result.decision.error_code == "origin_unknown"


async def test_cancel_during_body_releases_permit_in_postgres(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _bucket(pg_sessions)
    started = asyncio.Event()

    async def hang():
        yield b"x"
        started.set()
        await asyncio.Event().wait()

    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, content=hang()))
        task = asyncio.create_task(
            _fetcher(pg_sessions, "fetch-replica-1").fetch(FetchRequest(URL))
        )
        await asyncio.wait_for(started.wait(), 10)
        assert await _live(pg_sessions) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert await _live(pg_sessions) == 0


async def test_block_until_matches_retry_after(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _bucket(pg_sessions)
    before = datetime.now(UTC)

    with respx.mock:
        respx.get(URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "300"}, content=b"")
        )
        await _fetcher(pg_sessions, "fetch-replica-1").fetch(FetchRequest(URL))

    async with pg_sessions() as session:
        bucket = await limiter.get_bucket(session, ORIGIN)
    assert bucket is not None and bucket.blocked_until is not None
    delta = (bucket.blocked_until - before).total_seconds()
    assert 299 <= delta <= 330
