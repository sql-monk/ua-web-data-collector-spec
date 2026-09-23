"""Fetch/parse lineage і artifact pointers (§9.1 рядки 5–6, §10 п.5, п.7; R-27, R-38, R-41).

Таблиці: `fetches`, `raw_objects`, `parse_attempts`, `artifact_upload_claims`,
`normalized_artifacts`.

Принципи:

- **жодного domain payload** (R-27): PostgreSQL зберігає лише URI/hash/size/media type/schema
  version і lineage; самі bytes живуть у S3/MinIO;
- **lineage переживає видалення об'єкта** (§9.1: «видалення raw object … не повинно руйнувати
  lineage record»), тому `fetches.raw_sha256`, `parse_attempts.raw_sha256` і
  `normalized_artifacts.raw_sha256/raw_uri` — звичайні колонки **без FK**: retention видаляє
  рядок `raw_objects`, а слід у fetch/parse лишається читабельним;
- **upload claim — fencing token** (§10 п.5, R-38/R-41): unique `object_key`, монотонна
  `claim_generation`, lease; commit можливий лише під предикатом
  `generation + owner + lease_expires_at > now`.

Партиціонування: `fetches` — RANGE по `fetched_at` (місяць). `raw_objects` **не**
партиціонується свідомо: її ключ — `sha256` вмісту (§9.3 п.4 «однакові bytes фізично не
дублюються»), а declarative partitioning у PostgreSQL не вміє unique без partition key у
ключі — місячні партиції зробили б дедуплікацію помісячною, тобто не дедуплікацією. Кількість
рядків тут обмежена кількістю *різних* тіл, а не спроб (спроби — у `fetches`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
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

from collector.contracts.enums import ContentAccess, DataDomain, FetchOutcome, UploadClaimStatus
from collector.persistence.postgres.models.base import (
    Base,
    CreatedAt,
    UpdatedAt,
    UuidPk,
    enum_check,
)

PARSE_OUTCOMES: tuple[str, ...] = ("succeeded", "partial", "failed", "skipped")
"""Результат однієї спроби парсингу: `partial` — частина записів валідна, `skipped` — parser
свідомо нічого не видав (наприклад, 304/`metadata_only` без тіла)."""

SHA256_LENGTH = 64


class Fetch(Base):
    """Одна HTTP-спроба (§9.1): requested/final URL, HTTP metadata, результат, час.

    PK `(fetch_id, fetched_at)` — partition key обов'язковий у PK партиційованої таблиці.
    Посилання на неї з інших таблиць — за `fetch_id` **без FK** (FK на партиційовану таблицю
    вимагав би тягнути `fetched_at` у кожну дочірню таблицю).
    """

    __tablename__ = "fetches"
    __table_args__ = (
        enum_check("outcome", FetchOutcome, "outcome"),
        enum_check("content_access", ContentAccess, "content_access"),
        CheckConstraint(
            "http_status IS NULL OR (http_status BETWEEN 100 AND 599)", name="http_status_range"
        ),
        CheckConstraint(
            f"raw_sha256 IS NULL OR raw_sha256 ~ '^[0-9a-f]{{{SHA256_LENGTH}}}$'",
            name="raw_sha256_hex",
        ),
        Index("ix_fetches_job_id", "job_id"),
        Index("ix_fetches_source_id_fetched_at", "source_id", "fetched_at"),
        Index("ix_fetches_raw_sha256", "raw_sha256"),
        {"postgresql_partition_by": "RANGE (fetched_at)"},
    )

    fetch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    job_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text)
    request_variant: Mapped[str | None] = mapped_column(String(64))
    http_method: Mapped[str] = mapped_column(String(8), nullable=False, server_default="GET")
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    content_access: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=ContentAccess.UNKNOWN.value
    )
    content_type: Mapped[str | None] = mapped_column(String(256))
    content_encoding: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified_raw: Mapped[str | None] = mapped_column(String(128))
    response_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    raw_sha256: Mapped[str | None] = mapped_column(String(SHA256_LENGTH))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[CreatedAt]


class RawObject(Base):
    """Immutable raw artifact, ключ — `sha256(body)` (§9.3 п.4).

    `object_key` — шлях у artifact store (content-addressed), за яким sweeper §10 п.5 шукає
    orphan-об'єкти; `first_fetch_id` — перший fetch, що приніс ці bytes (lineage).
    """

    __tablename__ = "raw_objects"
    __table_args__ = (
        UniqueConstraint("object_key"),
        CheckConstraint(f"sha256 ~ '^[0-9a-f]{{{SHA256_LENGTH}}}$'", name="sha256_hex"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        Index("ix_raw_objects_first_seen_at", "first_seen_at"),
    )

    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH), primary_key=True)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_encoding: Mapped[str | None] = mapped_column(String(64))
    first_fetch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    first_seen_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[CreatedAt]


class ParseAttempt(Base):
    """Одна спроба парсингу raw object конкретною версією парсера (§9.1, §10 п.7)."""

    __tablename__ = "parse_attempts"
    __table_args__ = (
        enum_check("outcome", PARSE_OUTCOMES, "outcome"),
        enum_check("domain", DataDomain, "domain"),
        CheckConstraint(
            "records_count >= 0 AND validation_errors_count >= 0", name="counts_non_negative"
        ),
        Index("ix_parse_attempts_fetch_id", "fetch_id"),
        Index("ix_parse_attempts_raw_sha256", "raw_sha256"),
        Index("ix_parse_attempts_created_at", "created_at"),
    )

    parse_attempt_id: Mapped[UuidPk]
    fetch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    job_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    raw_sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    records_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    validation_errors_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(2048))
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]


class ArtifactUploadClaim(Base):
    """Claimed/verified PUT protocol (§10 п.5, R-38/R-41).

    Інваріанти схеми:

    - `object_key` unique — один власник на ключ у будь-який момент;
    - `claim_generation` монотонно зростає при кожному reacquire (fencing token);
    - `status='committed'` **є** DB reference: саме її відсутність (плюс відсутність живого
      lease) робить об'єкт кандидатом на видалення для sweeper.
    """

    __tablename__ = "artifact_upload_claims"
    __table_args__ = (
        UniqueConstraint("object_key"),
        enum_check("status", UploadClaimStatus, "status"),
        CheckConstraint("claim_generation >= 1", name="generation_positive"),
        CheckConstraint(
            "(status = 'committed') = (committed_at IS NOT NULL AND sha256 IS NOT NULL"
            " AND uri IS NOT NULL AND size_bytes IS NOT NULL)",
            name="committed_consistent",
        ),
        CheckConstraint(
            f"sha256 IS NULL OR sha256 ~ '^[0-9a-f]{{{SHA256_LENGTH}}}$'", name="sha256_hex"
        ),
        Index(
            "ix_artifact_upload_claims_lease_expires_at",
            "lease_expires_at",
            postgresql_where=text("status = 'leased'"),
        ),
        Index(
            "ix_artifact_upload_claims_orphan_candidates",
            "lease_expires_at",
            postgresql_where=text("status <> 'committed'"),
        ),
    )

    claim_id: Mapped[UuidPk]
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=UploadClaimStatus.LEASED.value
    )
    claim_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    acquired_at: Mapped[datetime] = mapped_column(nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(nullable=False)
    committed_at: Mapped[datetime | None]
    released_at: Mapped[datetime | None]
    sha256: Mapped[str | None] = mapped_column(String(SHA256_LENGTH))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uri: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class NormalizedArtifact(Base):
    """Pointer на immutable normalized projection artifact (§7.3, §9.1; R-27).

    Поля дзеркалять контракт `collector.contracts.NormalizedArtifactRef` — payload лишається у
    S3/MinIO, у PostgreSQL тільки посилання, hash, схема і lineage до raw/fetch/parse.
    """

    __tablename__ = "normalized_artifacts"
    __table_args__ = (
        UniqueConstraint("object_key"),
        UniqueConstraint("sha256", "entity_uuid"),
        enum_check("domain", DataDomain, "domain"),
        CheckConstraint(f"sha256 ~ '^[0-9a-f]{{{SHA256_LENGTH}}}$'", name="sha256_hex"),
        CheckConstraint(
            f"raw_sha256 ~ '^[0-9a-f]{{{SHA256_LENGTH}}}$'", name="raw_sha256_hex"
        ),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        Index("ix_normalized_artifacts_entity_uuid_produced_at", "entity_uuid", "produced_at"),
        Index("ix_normalized_artifacts_raw_sha256", "raw_sha256"),
    )

    artifact_id: Mapped[UuidPk]
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    entity_uuid: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("parse_attempts.parse_attempt_id", ondelete="SET NULL")
    )
    fetch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    raw_sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH), nullable=False)
    raw_uri: Mapped[str] = mapped_column(Text, nullable=False)
    is_metadata_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    produced_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[CreatedAt]
