"""SQLAlchemy-моделі таблиць §9.1 — джерело істини для Alembic autogenerate/`alembic check`.

Імпорт цього пакета реєструє всі таблиці у `Base.metadata`; нові модулі (PR2: artifacts/
projection/outbox, PR3: news/matching/release) додаються сюди ж.
"""

from __future__ import annotations

from collector.persistence.postgres.models.audit import AuditLog
from collector.persistence.postgres.models.base import Base
from collector.persistence.postgres.models.control import (
    ROUTE_KINDS,
    Source,
    SourceCursor,
    SourcePolicyVersion,
    SourceRoute,
)
from collector.persistence.postgres.models.limiter import (
    PERMIT_RELEASE_REASONS,
    OriginRateBucket,
    OriginRatePermit,
)
from collector.persistence.postgres.models.pools import (
    INSTANCE_STATUSES,
    POOL_MODES,
    SCALE_COMMAND_STATUSES,
    SCALE_COMMAND_TERMINAL,
    SCALE_COMMAND_TRANSITIONS,
    ScaleCommand,
    WorkerInstance,
    WorkerPool,
)
from collector.persistence.postgres.models.queue import (
    CLAIMABLE_JOB_STATUSES,
    CRAWL_RUN_KINDS,
    CRAWL_RUN_STATUSES,
    DEAD_LETTER_REASONS,
    JOB_ARGS_MAX_BYTES,
    JOB_STATUSES,
    CrawlJob,
    CrawlRun,
    DeadLetter,
)

__all__ = [
    "CLAIMABLE_JOB_STATUSES",
    "CRAWL_RUN_KINDS",
    "CRAWL_RUN_STATUSES",
    "DEAD_LETTER_REASONS",
    "INSTANCE_STATUSES",
    "JOB_ARGS_MAX_BYTES",
    "JOB_STATUSES",
    "PERMIT_RELEASE_REASONS",
    "POOL_MODES",
    "ROUTE_KINDS",
    "SCALE_COMMAND_STATUSES",
    "SCALE_COMMAND_TERMINAL",
    "SCALE_COMMAND_TRANSITIONS",
    "AuditLog",
    "Base",
    "CrawlJob",
    "CrawlRun",
    "DeadLetter",
    "OriginRateBucket",
    "OriginRatePermit",
    "ScaleCommand",
    "Source",
    "SourceCursor",
    "SourcePolicyVersion",
    "SourceRoute",
    "WorkerInstance",
    "WorkerPool",
]
