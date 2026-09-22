"""Control plane джерел (§9.1 рядок 1): `sources`, `source_policy_versions`, `source_routes`,
`source_cursors`.

- `sources.source_id` — canonical id з `docs/research/source-registry.yaml` (§9.3 п.1);
  `state` — `SourceState` (§5.5), `revision` — optimistic (§7.6);
- `source_policy_versions` — immutable snapshot policy-частини маніфесту (Додаток B): explicit
  колонки лімітера/розкладу + `manifest_sha256`/`manifest_uri` замість JSONB-копії;
- `source_routes.state` — `RouteState` (§5.5) з circuit breaker полями;
- `source_cursors.cursor_value` — opaque TEXT (page token, last-modified, watermark);
  структурований payload не потрібен, тому без JSONB.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from collector.contracts.enums import DataDomain, RouteState, SourceState
from collector.persistence.postgres.models.base import (
    Base,
    CreatedAt,
    Revision,
    UpdatedAt,
    UuidPk,
    enum_check,
)

ROUTE_KINDS: tuple[str, ...] = ("rss", "sitemap", "category", "detail", "api", "browser")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("source_id"),
        enum_check("state", SourceState, "state"),
        enum_check("domain", DataDomain, "domain"),
    )

    id: Mapped[UuidPk]
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=SourceState.PAUSED.value
    )
    state_reason: Mapped[str | None] = mapped_column(String(512))
    current_policy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_policy_versions.id", use_alter=True, ondelete="SET NULL")
    )
    revision: Mapped[Revision]
    updated_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class SourcePolicyVersion(Base):
    __tablename__ = "source_policy_versions"
    __table_args__ = (UniqueConstraint("source_id", "version"),)

    id: Mapped[UuidPk]
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    requests_per_second: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    burst_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    crawl_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    browser_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    robots_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_uri: Mapped[str | None] = mapped_column(String(1024))
    effective_from: Mapped[datetime] = mapped_column(nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[CreatedAt]


class SourceRoute(Base):
    __tablename__ = "source_routes"
    __table_args__ = (
        UniqueConstraint("source_id", "route_kind", "route_key"),
        enum_check("state", RouteState, "state"),
        enum_check("route_kind", ROUTE_KINDS, "route_kind"),
        Index("ix_source_routes_state", "state", "circuit_open_until"),
    )

    id: Mapped[UuidPk]
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    route_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    route_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=RouteState.HEALTHY.value
    )
    state_reason: Mapped[str | None] = mapped_column(String(512))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    circuit_open_until: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_failure_at: Mapped[datetime | None]
    revision: Mapped[Revision]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class SourceCursor(Base):
    __tablename__ = "source_cursors"
    __table_args__ = (UniqueConstraint("source_id", "cursor_kind", "cursor_key"),)

    id: Mapped[UuidPk]
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    route_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_routes.id", ondelete="SET NULL")
    )
    cursor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    cursor_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    cursor_value: Mapped[str] = mapped_column(String(4096), nullable=False)
    cursor_at: Mapped[datetime | None]
    revision: Mapped[Revision]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
