"""Versioned robots snapshots and policy evaluation.

Snapshots are ordinary immutable raw artifacts.  PostgreSQL fetch history supplies the TTL
and lineage; S3 supplies the exact bytes used for a decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.persistence.postgres.models import Fetch, RawObject

DEFAULT_ROBOTS_TTL = timedelta(hours=24)
# Same decoded/raw ceiling as ordinary HTML.  A lower read ceiling would let an explicitly
# snapshotted robots file succeed, then fail every subsequent `respect` decision while loading.
MAX_ROBOTS_BYTES = 20 * 1024 * 1024
ROBOTS_POLICIES = frozenset({"diagnostic", "ignore", "respect"})


@dataclass(frozen=True, slots=True)
class RobotsSnapshot:
    fetched_at: datetime
    http_status: int | None
    sha256: str | None
    object_key: str | None

    def fresh(self, now: datetime, ttl: timedelta) -> bool:
        return now - self.fetched_at < ttl


def robots_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


def robots_allows(policy: str, body: bytes | None, url: str, user_agent: str) -> bool:
    """Evaluate a snapshot. Diagnostic/ignore policies never block collection."""
    if policy not in ROBOTS_POLICIES:
        raise ValueError(f"unknown robots policy {policy!r}")
    if policy != "respect" or body is None:
        return True
    parser = RobotFileParser()
    parser.set_url(robots_url(url))
    parser.parse(body.decode("utf-8", errors="replace").splitlines())
    return parser.can_fetch(user_agent, url)


async def latest_robots_snapshot(
    session: AsyncSession, source_id: UUID, url: str
) -> RobotsSnapshot | None:
    """Return latest robots attempt plus the newest available raw snapshot, if any."""
    target = robots_url(url)
    latest = (
        await session.execute(
            select(Fetch.fetched_at, Fetch.http_status, Fetch.raw_sha256)
            .where(
                Fetch.source_id == source_id,
                Fetch.requested_url == target,
                Fetch.request_variant == "robots",
                Fetch.http_status.in_((200, 304, 404)),
            )
            .order_by(Fetch.fetched_at.desc())
            .limit(1)
        )
    ).one_or_none()
    if latest is None:
        return None
    fetched_at, status, sha256 = latest._tuple()
    object_key = None
    if sha256 is not None:
        object_key = await session.scalar(
            select(RawObject.object_key).where(RawObject.sha256 == sha256)
        )
    if object_key is None:
        content = (
            await session.execute(
                select(Fetch.raw_sha256, RawObject.object_key)
                .join(RawObject, RawObject.sha256 == Fetch.raw_sha256)
                .where(
                    Fetch.source_id == source_id,
                    Fetch.requested_url == target,
                    Fetch.request_variant == "robots",
                    Fetch.raw_sha256.is_not(None),
                )
                .order_by(Fetch.fetched_at.desc())
                .limit(1)
            )
        ).one_or_none()
        if content is not None:
            sha256, object_key = content._tuple()
    return RobotsSnapshot(fetched_at, status, sha256, object_key)


__all__ = [
    "DEFAULT_ROBOTS_TTL",
    "MAX_ROBOTS_BYTES",
    "ROBOTS_POLICIES",
    "RobotsSnapshot",
    "latest_robots_snapshot",
    "robots_allows",
    "robots_url",
]
