"""Projection tasks/acks і global entity index (§7.3 кроки 2–4, §9.1, §9.5; R-36).

Таблиці: `projection_tasks`, `projection_acknowledgements`, `entity_index`.

- `projection_tasks` — черга projector-а з тією самою lease-семантикою, що й `crawl_jobs`;
  unique `(entity_uuid, projection_version)` (монотонна версія видається атомарно) і unique
  `parse_key` — ідентичність **parse-кроку** (fetch + raw + parser_version + entity +
  target collection; gate 3, CR-1): повтор того самого parse повертає той самий task, а новий
  parse з byte-identical artifact (стан A→B→A) отримує нову версію й посилається на вже
  наявний рядок `normalized_artifacts` (дедуплікація за вмістом лишається для artifacts);
- `projection_acknowledgements` — PK `task_id`: повторний ack ідемпотентний за побудовою;
- `entity_index` — PK `entity_uuid`, unique source identity (§9.3 п.1), лічильники версій:
  `projection_version` (остання **видана**) і `confirmed_projection_version` (остання
  **підтверджена** Mongo receipt-ом, §9.5 — ніколи не зменшується). Domain document тут не
  дублюється (§9.1) — лише посилання на Mongo collection/document id.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from collector.contracts.enums import DataDomain
from collector.persistence.postgres.models.base import (
    Base,
    CreatedAt,
    UpdatedAt,
    UuidPk,
    enum_check,
)

PROJECTION_TASK_STATUSES: tuple[str, ...] = (
    "pending",
    "leased",
    "succeeded",
    "retry",
    "quarantined",
)
"""Ті самі статуси, що й у `crawl_jobs` (§9.1) — projector користується тією ж моделлю lease."""

CLAIMABLE_PROJECTION_STATUSES: tuple[str, ...] = ("pending", "retry")
CLAIMABLE_PROJECTION_PREDICATE = "status IN ({})".format(
    ", ".join(f"'{status}'" for status in CLAIMABLE_PROJECTION_STATUSES)
)
"""Предикат partial index `ix_projection_tasks_claimable_order`; має збігатися зі статусами,
які бере `repositories.projection.claim_projection_tasks`."""

STATE_HASH_PATTERN = "^v[1-9][0-9]*:[0-9a-f]{64}$"
"""`collector.contracts.projection.StateHash` — versioned `v1:<sha256>`."""


class ProjectionTask(Base):
    """Одна task projector-а: artifact → цільова Mongo collection у версії `projection_version`."""

    __tablename__ = "projection_tasks"
    __table_args__ = (
        UniqueConstraint("entity_uuid", "projection_version"),
        UniqueConstraint("parse_key"),
        CheckConstraint("parse_key ~ '^[0-9a-f]{64}$'", name="parse_key_hex"),
        enum_check("status", PROJECTION_TASK_STATUSES, "status"),
        CheckConstraint("projection_version >= 1", name="version_positive"),
        CheckConstraint("attempt >= 0 AND max_attempts >= 1", name="attempts"),
        CheckConstraint(
            "(status = 'leased') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_consistent",
        ),
        # Обов'язковий index §9.1: операторські вибірки і фільтри за (status, not_before).
        Index(
            "ix_projection_tasks_status_not_before_priority",
            "status",
            "not_before",
            "priority",
            "task_id",
        ),
        # Hot path claim: лише claimable-рядки, у точному порядку ORDER BY (той самий урок,
        # що й `ix_crawl_jobs_claimable_order` у PR1 — status у предикаті, не в ключі).
        Index(
            "ix_projection_tasks_claimable_order",
            text("priority DESC"),
            "not_before",
            "task_id",
            postgresql_where=text(CLAIMABLE_PROJECTION_PREDICATE),
        ),
        Index(
            "ix_projection_tasks_lease_expires_at",
            "lease_expires_at",
            postgresql_where=text("status = 'leased'"),
        ),
        Index("ix_projection_tasks_entity_uuid", "entity_uuid"),
        Index("ix_projection_tasks_artifact_id", "artifact_id"),
    )

    task_id: Mapped[UuidPk]
    artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("normalized_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    entity_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("entity_index.entity_uuid", ondelete="RESTRICT"), nullable=False
    )
    parse_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("parse_attempts.parse_attempt_id", ondelete="SET NULL")
    )
    parse_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_collection: Mapped[str] = mapped_column(String(120), nullable=False)
    target_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("100"))
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


class ProjectionAcknowledgement(Base):
    """PostgreSQL-фіксація Mongo receipt (§7.3 п.4, §9.1).

    PK = `task_id`: повторний ack того самого task не створює другого рядка — саме це робить
    крок 4 ідемпотентним після crash між Mongo commit і PostgreSQL commit.
    """

    __tablename__ = "projection_acknowledgements"
    __table_args__ = (
        CheckConstraint("projection_version >= 1", name="version_positive"),
        CheckConstraint(f"result_hash ~ '{STATE_HASH_PATTERN}'", name="result_hash_format"),
        CheckConstraint(
            "event_sha256 IS NULL OR event_sha256 ~ '^[0-9a-f]{64}$'", name="event_sha256_hex"
        ),
        Index("ix_projection_acknowledgements_entity_uuid", "entity_uuid", "projection_version"),
        Index("ix_projection_acknowledgements_acknowledged_at", "acknowledged_at"),
    )

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("projection_tasks.task_id", ondelete="RESTRICT"), primary_key=True
    )
    entity_uuid: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    projection_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    receipt_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    receipt_cluster_time: Mapped[str] = mapped_column(String(64), nullable=False)
    mongo_document_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    applied_to_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state_changed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(80))
    event_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    event_sha256: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[CreatedAt]


class EntityIndex(Base):
    """Global cross-domain index (§9.1): source identity → entity_uuid → Mongo document.

    `source_item_id` — природний ключ джерела (§9.3 п.1); якщо джерело його не дає, викликач
    кладе сюди versioned `identity_hash` (§9.3 п.2) і дублює його в `identity_hash` для
    provenance. Так unique source identity лишається одним index-ом, без часткових unique.
    """

    __tablename__ = "entity_index"
    __table_args__ = (
        UniqueConstraint("source_id", "source_item_id"),
        enum_check("domain", DataDomain, "domain"),
        CheckConstraint(
            "confirmed_projection_version >= 0 AND projection_version >= 0", name="versions"
        ),
        CheckConstraint(
            "confirmed_projection_version <= projection_version", name="confirmed_not_ahead"
        ),
        # Обов'язковий index §9.1 + keyset pagination §15 (`list_entities`).
        Index(
            "ix_entity_index_domain_confirmed_version",
            "domain",
            "confirmed_projection_version",
            "entity_uuid",
        ),
        Index("ix_entity_index_updated_at", "updated_at"),
    )

    entity_uuid: Mapped[UuidPk]
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_item_id: Mapped[str] = mapped_column(String(512), nullable=False)
    identity_hash: Mapped[str | None] = mapped_column(String(80))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    mongo_collection: Mapped[str | None] = mapped_column(String(120))
    mongo_document_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    projection_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    confirmed_projection_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    confirmed_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
