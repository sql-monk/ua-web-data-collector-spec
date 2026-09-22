"""Глобальний origin limiter §7.6 / FR-033 / R-53: `origin_rate_buckets`, `origin_rate_permits`.

Canonical облік у PostgreSQL — per-container semaphore лише додатково. Rate tokens
(token bucket: `capacity_tokens`, `refill_per_second`, `available_tokens`, `last_refill_at`)
і concurrency (`max_concurrency` проти live permits) обліковуються окремо: permit видається
лише коли доступні обидва. Live permit = `released_at IS NULL AND lease_expires_at > now`.
`blocked_until` — 429/`Retry-After`/challenge: жоден permit до цього моменту.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from collector.persistence.postgres.models.base import (
    Base,
    CreatedAt,
    Revision,
    UpdatedAt,
    UuidPk,
    enum_check,
)

PERMIT_RELEASE_REASONS: tuple[str, ...] = ("released", "expired")


class OriginRateBucket(Base):
    __tablename__ = "origin_rate_buckets"
    __table_args__ = (
        CheckConstraint("capacity_tokens > 0 AND refill_per_second > 0", name="positive_rate"),
        CheckConstraint("max_concurrency >= 0", name="concurrency"),
        CheckConstraint(
            "available_tokens >= 0 AND available_tokens <= capacity_tokens", name="tokens_range"
        ),
    )

    origin: Mapped[str] = mapped_column(String(512), primary_key=True)
    capacity_tokens: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    refill_per_second: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    available_tokens: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    last_refill_at: Mapped[datetime] = mapped_column(nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    blocked_until: Mapped[datetime | None]
    block_reason: Mapped[str | None] = mapped_column(String(256))
    revision: Mapped[Revision]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class OriginRatePermit(Base):
    __tablename__ = "origin_rate_permits"
    __table_args__ = (
        enum_check("release_reason", PERMIT_RELEASE_REASONS, "release_reason"),
        CheckConstraint(
            "(released_at IS NULL) = (release_reason IS NULL)", name="release_consistent"
        ),
        Index("ix_origin_rate_permits_origin_lease_expires_at", "origin", "lease_expires_at"),
    )

    permit_id: Mapped[UuidPk]
    origin: Mapped[str] = mapped_column(
        ForeignKey("origin_rate_buckets.origin", ondelete="CASCADE"), nullable=False
    )
    owner_instance: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("crawl_jobs.job_id", ondelete="SET NULL")
    )
    acquired_at: Mapped[datetime] = mapped_column(nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(nullable=False)
    released_at: Mapped[datetime | None]
    release_reason: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[CreatedAt]
