"""Job queue §7.2 і crawl runs FR-002: `crawl_runs`, `crawl_jobs`, `dead_letters`.

`crawl_jobs.status` (§9.1): `pending | leased | succeeded | retry | quarantined`.

- `pending` — готовий до claim, коли `not_before <= now`;
- `leased` — у роботі: `lease_owner`/`lease_expires_at`, heartbeat продовжує lease;
- `retry` — спроба завершилась `retryable`, чекає `not_before` (backoff + jitter); claim бере
  і `pending`, і `retry` — обидва є «claimable»;
- `succeeded` — термінальний;
- `quarantined` — термінальний після `max_attempts` або явного `quarantine`; запис у
  `dead_letters`.

`attempt` збільшується при claim; `recover_expired_leases` повертає прострочені `leased` у
`pending`, зберігаючи `attempt`. `args` — єдиний JSONB черги: bounded (≤ 8 KiB) аргументи job
(URL, request variant, route) — control extension, не domain payload (R-27).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from collector.persistence.postgres.models.base import (
    Base,
    CreatedAt,
    UpdatedAt,
    UuidPk,
    enum_check,
)

CRAWL_RUN_KINDS: tuple[str, ...] = ("full", "incremental", "replay", "backfill")
CRAWL_RUN_STATUSES: tuple[str, ...] = ("running", "succeeded", "failed", "cancelled")
JOB_STATUSES: tuple[str, ...] = ("pending", "leased", "succeeded", "retry", "quarantined")
CLAIMABLE_JOB_STATUSES: tuple[str, ...] = ("pending", "retry")
DEAD_LETTER_REASONS: tuple[str, ...] = ("max_attempts", "quarantine")
JOB_ARGS_MAX_BYTES = 8 * 1024


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (
        enum_check("kind", CRAWL_RUN_KINDS, "kind"),
        enum_check("status", CRAWL_RUN_STATUSES, "status"),
        # FR-002: не більше одного running повного обходу на джерело.
        Index(
            "uq_crawl_runs_running_full",
            "source_id",
            unique=True,
            postgresql_where=text("status = 'running' AND kind = 'full'"),
        ),
        Index("ix_crawl_runs_source_id_started_at", "source_id", "started_at"),
    )

    id: Mapped[UuidPk]
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    policy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_policy_versions.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="running")
    started_by: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    finished_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        enum_check("status", JOB_STATUSES, "status"),
        CheckConstraint("attempt >= 0 AND max_attempts >= 1", name="attempts"),
        CheckConstraint(f"octet_length(args::text) <= {JOB_ARGS_MAX_BYTES}", name="args_size"),
        CheckConstraint(
            "(status = 'leased') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_consistent",
        ),
        Index(
            "ix_crawl_jobs_status_not_before_priority", "status", "not_before", "priority", "job_id"
        ),
        Index(
            "ix_crawl_jobs_lease_expires_at",
            "lease_expires_at",
            postgresql_where=text("status = 'leased'"),
        ),
        Index("ix_crawl_jobs_run_id", "run_id"),
    )

    job_id: Mapped[UuidPk]
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("crawl_runs.id", ondelete="SET NULL"))
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("100"))
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    not_before: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None]
    leased_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class DeadLetter(Base):
    __tablename__ = "dead_letters"
    __table_args__ = (
        enum_check("reason", DEAD_LETTER_REASONS, "reason"),
        Index("ix_dead_letters_job_id", "job_id"),
        Index("ix_dead_letters_created_at", "created_at"),
    )

    id: Mapped[UuidPk]
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("crawl_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[CreatedAt]
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolution: Mapped[str | None] = mapped_column(String(512))
