"""WP-01A PR2: artifacts, upload claims, projection tasks/acks, outboxes, entity index.

Таблиці §9.1: `fetches` (RANGE-партиції по `fetched_at` + DEFAULT-партиція), `raw_objects`,
`parse_attempts`, `artifact_upload_claims`, `normalized_artifacts`, `projection_tasks`,
`projection_acknowledgements`, `entity_index`, `change_events`, `outbox_events`.

Обов'язкові indexes (§9.1): `projection_tasks(status, not_before, priority, task_id)`,
`outbox_events(published_at, available_at, event_id)`,
`entity_index(domain, confirmed_projection_version, entity_uuid)` — плюс partial indexes під
hot paths (claim projection tasks, публікація outbox, orphan candidates), той самий підхід, що
`ix_crawl_jobs_claimable_order` у `0002`.

Ключові unique-обмеження, від яких залежить коректність потоку §7.3:

- `normalized_artifacts(object_key)` — ідемпотентність `record_parse_result`;
- `projection_tasks(entity_uuid, projection_version)` — монотонна версія без дублів (R-36);
- `projection_tasks(artifact_id, target_collection)` — artifact projection key;
- `artifact_upload_claims(object_key)` — один власник ключа (R-38/R-41);
- `change_events(event_id)`, `outbox_events(event_id)` — глобальна дедуплікація подій
  (R-42). Саме через них ці дві таблиці **не** партиціоновані — див. docstring
  `collector/persistence/postgres/models/outbox.py`.

`raw_objects` теж не партиціонована: PK — `sha256(body)` (§9.3 п.4), і місячні партиції
зробили б дедуплікацію помісячною.

Downgrade реалізовано (PR2 додає лише нові таблиці; для production forward-only, див. картку
«Rollback/disable»).

Revision ID: 0004_artifacts_projection
Revises: 0003_default_partition
Create Date: 2026-09-23 14:20 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_artifacts_projection"
down_revision: str | Sequence[str] | None = "0003_default_partition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FETCHES_DEFAULT_PARTITION = "fetches_default"


def upgrade() -> None:
    op.create_table(
        "artifact_upload_claims",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="leased", nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("media_type", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'committed') = (committed_at IS NOT NULL AND sha256 IS NOT NULL"
            " AND uri IS NOT NULL AND size_bytes IS NOT NULL)",
            name=op.f("ck_artifact_upload_claims_committed_consistent"),
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_artifact_upload_claims_sha256_hex"),
        ),
        sa.CheckConstraint(
            "status IN ('leased', 'committed', 'released', 'expired')",
            name=op.f("ck_artifact_upload_claims_status"),
        ),
        sa.CheckConstraint(
            "claim_generation >= 1", name=op.f("ck_artifact_upload_claims_generation_positive")
        ),
        sa.PrimaryKeyConstraint("claim_id", name=op.f("pk_artifact_upload_claims")),
        sa.UniqueConstraint("object_key", name=op.f("uq_artifact_upload_claims_object_key")),
    )
    op.create_index(
        "ix_artifact_upload_claims_lease_expires_at",
        "artifact_upload_claims",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.create_index(
        "ix_artifact_upload_claims_orphan_candidates",
        "artifact_upload_claims",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status <> 'committed'"),
    )
    op.create_table(
        "entity_index",
        sa.Column("entity_uuid", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_item_id", sa.String(length=512), nullable=False),
        sa.Column("identity_hash", sa.String(length=80), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("mongo_collection", sa.String(length=120), nullable=True),
        sa.Column("mongo_document_id", sa.Uuid(), nullable=True),
        sa.Column(
            "projection_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "confirmed_projection_version",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "domain IN ('news', 'vehicle', 'catalog')", name=op.f("ck_entity_index_domain")
        ),
        sa.CheckConstraint(
            "confirmed_projection_version <= projection_version",
            name=op.f("ck_entity_index_confirmed_not_ahead"),
        ),
        sa.CheckConstraint(
            "confirmed_projection_version >= 0 AND projection_version >= 0",
            name=op.f("ck_entity_index_versions"),
        ),
        sa.PrimaryKeyConstraint("entity_uuid", name=op.f("pk_entity_index")),
        sa.UniqueConstraint(
            "source_id", "source_item_id", name=op.f("uq_entity_index_source_id_source_item_id")
        ),
    )
    op.create_index(
        "ix_entity_index_domain_confirmed_version",
        "entity_index",
        ["domain", "confirmed_projection_version", "entity_uuid"],
        unique=False,
    )
    op.create_index("ix_entity_index_updated_at", "entity_index", ["updated_at"], unique=False)
    op.create_table(
        "fetches",
        sa.Column("fetch_id", sa.Uuid(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("request_variant", sa.String(length=64), nullable=True),
        sa.Column("http_method", sa.String(length=8), server_default="GET", nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("content_access", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("content_type", sa.String(length=256), nullable=True),
        sa.Column("content_encoding", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=512), nullable=True),
        sa.Column("last_modified_raw", sa.String(length=128), nullable=True),
        sa.Column("response_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_access IN ('full', 'partial', 'metadata_only', 'blocked', 'challenge',"
            " 'premium', 'gone', 'unknown')",
            name=op.f("ck_fetches_content_access"),
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'retryable', 'permanent_failure')",
            name=op.f("ck_fetches_outcome"),
        ),
        sa.CheckConstraint(
            "raw_sha256 IS NULL OR raw_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_fetches_raw_sha256_hex"),
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status BETWEEN 100 AND 599)",
            name=op.f("ck_fetches_http_status_range"),
        ),
        sa.PrimaryKeyConstraint("fetch_id", "fetched_at", name=op.f("pk_fetches")),
        postgresql_partition_by="RANGE (fetched_at)",
    )
    # DEFAULT-партиція (аргумент — `0003`): пропущене обслуговування не повинно знищувати
    # lineage вже виконаних HTTP-запитів. DDL заморожений у ревізії і не імпортує runtime-хелпер
    # `partitions` (S-4 пострев'ю PR1) — збіг імен перевіряє тест у `test_migrations.py`.
    op.execute(
        f"CREATE TABLE IF NOT EXISTS {FETCHES_DEFAULT_PARTITION} PARTITION OF fetches DEFAULT"
    )
    op.create_index("ix_fetches_job_id", "fetches", ["job_id"], unique=False)
    op.create_index("ix_fetches_raw_sha256", "fetches", ["raw_sha256"], unique=False)
    op.create_index(
        "ix_fetches_source_id_fetched_at", "fetches", ["source_id", "fetched_at"], unique=False
    )
    op.create_table(
        "outbox_events",
        sa.Column("outbox_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("payload_schema_version", sa.String(length=16), nullable=False),
        sa.Column("payload_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("payload_media_type", sa.String(length=128), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_artifact_uri", sa.Text(), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_outbox_events_payload_sha256_hex")
        ),
        sa.CheckConstraint("topic IN ('internal', 'domain')", name=op.f("ck_outbox_events_topic")),
        sa.CheckConstraint(
            "(payload_bytes IS NULL) <> (payload_artifact_uri IS NULL)",
            name=op.f("ck_outbox_events_inline_xor_artifact"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_events_attempts_non_negative")),
        sa.CheckConstraint(
            "payload_bytes IS NULL OR octet_length(payload_bytes) <= 262144",
            name=op.f("ck_outbox_events_inline_size"),
        ),
        sa.PrimaryKeyConstraint("outbox_id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_outbox_events_event_id")),
    )
    op.create_index(
        "ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"], unique=False
    )
    op.create_index(
        "ix_outbox_events_published_at_available_at",
        "outbox_events",
        ["published_at", "available_at", "event_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["available_at", "event_id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "parse_attempts",
        sa.Column("parse_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("fetch_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("records_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "validation_errors_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "domain IN ('news', 'vehicle', 'catalog')", name=op.f("ck_parse_attempts_domain")
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'partial', 'failed', 'skipped')",
            name=op.f("ck_parse_attempts_outcome"),
        ),
        sa.CheckConstraint(
            "records_count >= 0 AND validation_errors_count >= 0",
            name=op.f("ck_parse_attempts_counts_non_negative"),
        ),
        sa.PrimaryKeyConstraint("parse_attempt_id", name=op.f("pk_parse_attempts")),
    )
    op.create_index("ix_parse_attempts_created_at", "parse_attempts", ["created_at"], unique=False)
    op.create_index("ix_parse_attempts_fetch_id", "parse_attempts", ["fetch_id"], unique=False)
    op.create_index("ix_parse_attempts_raw_sha256", "parse_attempts", ["raw_sha256"], unique=False)
    op.create_table(
        "raw_objects",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("content_encoding", sa.String(length=64), nullable=True),
        sa.Column("first_fetch_id", sa.Uuid(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_raw_objects_sha256_hex")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_raw_objects_size_non_negative")),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_raw_objects")),
        sa.UniqueConstraint("object_key", name=op.f("uq_raw_objects_object_key")),
    )
    op.create_index("ix_raw_objects_first_seen_at", "raw_objects", ["first_seen_at"], unique=False)
    op.create_table(
        "normalized_artifacts",
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("entity_uuid", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("parse_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("fetch_id", sa.Uuid(), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_uri", sa.Text(), nullable=False),
        sa.Column(
            "is_metadata_only", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("produced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "domain IN ('news', 'vehicle', 'catalog')", name=op.f("ck_normalized_artifacts_domain")
        ),
        sa.CheckConstraint(
            "raw_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_normalized_artifacts_raw_sha256_hex")
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_normalized_artifacts_sha256_hex")
        ),
        sa.CheckConstraint(
            "size_bytes >= 0", name=op.f("ck_normalized_artifacts_size_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["parse_attempt_id"],
            ["parse_attempts.parse_attempt_id"],
            name=op.f("fk_normalized_artifacts_parse_attempt_id_parse_attempts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("artifact_id", name=op.f("pk_normalized_artifacts")),
        sa.UniqueConstraint("object_key", name=op.f("uq_normalized_artifacts_object_key")),
        sa.UniqueConstraint(
            "sha256", "entity_uuid", name=op.f("uq_normalized_artifacts_sha256_entity_uuid")
        ),
    )
    op.create_index(
        "ix_normalized_artifacts_entity_uuid_produced_at",
        "normalized_artifacts",
        ["entity_uuid", "produced_at"],
        unique=False,
    )
    op.create_index(
        "ix_normalized_artifacts_raw_sha256", "normalized_artifacts", ["raw_sha256"], unique=False
    )
    op.create_table(
        "projection_tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("entity_uuid", sa.Uuid(), nullable=False),
        sa.Column("projection_version", sa.BigInteger(), nullable=False),
        sa.Column("target_collection", sa.String(length=120), nullable=False),
        sa.Column("target_schema_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("100"), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column(
            "not_before",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'leased') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_projection_tasks_lease_consistent"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', 'retry', 'quarantined')",
            name=op.f("ck_projection_tasks_status"),
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts >= 1", name=op.f("ck_projection_tasks_attempts")
        ),
        sa.CheckConstraint(
            "projection_version >= 1", name=op.f("ck_projection_tasks_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["normalized_artifacts.artifact_id"],
            name=op.f("fk_projection_tasks_artifact_id_normalized_artifacts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entity_uuid"],
            ["entity_index.entity_uuid"],
            name=op.f("fk_projection_tasks_entity_uuid_entity_index"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_projection_tasks")),
        sa.UniqueConstraint(
            "artifact_id",
            "target_collection",
            name=op.f("uq_projection_tasks_artifact_id_target_collection"),
        ),
        sa.UniqueConstraint(
            "entity_uuid",
            "projection_version",
            name=op.f("uq_projection_tasks_entity_uuid_projection_version"),
        ),
    )
    op.create_index(
        "ix_projection_tasks_claimable_order",
        "projection_tasks",
        [sa.literal_column("priority DESC"), "not_before", "task_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )
    op.create_index(
        "ix_projection_tasks_entity_uuid", "projection_tasks", ["entity_uuid"], unique=False
    )
    op.create_index(
        "ix_projection_tasks_lease_expires_at",
        "projection_tasks",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.create_index(
        "ix_projection_tasks_status_not_before_priority",
        "projection_tasks",
        ["status", "not_before", "priority", "task_id"],
        unique=False,
    )
    op.create_table(
        "change_events",
        sa.Column("change_event_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_schema_version", sa.String(length=16), nullable=False),
        sa.Column("projection_task_id", sa.Uuid(), nullable=False),
        sa.Column("previous_state_hash", sa.String(length=80), nullable=True),
        sa.Column("result_state_hash", sa.String(length=80), nullable=False),
        sa.Column("event_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("event_media_type", sa.String(length=128), nullable=False),
        sa.Column("event_sha256", sa.String(length=64), nullable=False),
        sa.Column("event_artifact_uri", sa.Text(), nullable=True),
        sa.Column("event_artifact_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_change_events_event_sha256_hex")
        ),
        sa.CheckConstraint(
            "(event_bytes IS NULL) <> (event_artifact_uri IS NULL)",
            name=op.f("ck_change_events_inline_xor_artifact"),
        ),
        sa.CheckConstraint(
            "aggregate_version >= 1", name=op.f("ck_change_events_aggregate_version_positive")
        ),
        sa.CheckConstraint(
            "event_bytes IS NULL OR octet_length(event_bytes) <= 262144",
            name=op.f("ck_change_events_inline_size"),
        ),
        sa.ForeignKeyConstraint(
            ["projection_task_id"],
            ["projection_tasks.task_id"],
            name=op.f("fk_change_events_projection_task_id_projection_tasks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("change_event_id", name=op.f("pk_change_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_change_events_event_id")),
    )
    op.create_index(
        "ix_change_events_aggregate_id_version",
        "change_events",
        ["aggregate_id", "aggregate_version"],
        unique=False,
    )
    op.create_index("ix_change_events_created_at", "change_events", ["created_at"], unique=False)
    op.create_table(
        "projection_acknowledgements",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("entity_uuid", sa.Uuid(), nullable=False),
        sa.Column("projection_version", sa.BigInteger(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_cluster_time", sa.String(length=64), nullable=False),
        sa.Column("mongo_document_id", sa.Uuid(), nullable=True),
        sa.Column("applied_to_current", sa.Boolean(), nullable=False),
        sa.Column("state_changed", sa.Boolean(), nullable=False),
        sa.Column("result_version", sa.BigInteger(), nullable=False),
        sa.Column("result_hash", sa.String(length=80), nullable=False),
        sa.Column("previous_hash", sa.String(length=80), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("event_sha256", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_sha256 IS NULL OR event_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_projection_acknowledgements_event_sha256_hex"),
        ),
        sa.CheckConstraint(
            "result_hash ~ '^v[1-9][0-9]*:[0-9a-f]{64}$'",
            name=op.f("ck_projection_acknowledgements_result_hash_format"),
        ),
        sa.CheckConstraint(
            "projection_version >= 1", name=op.f("ck_projection_acknowledgements_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["projection_tasks.task_id"],
            name=op.f("fk_projection_acknowledgements_task_id_projection_tasks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_projection_acknowledgements")),
    )
    op.create_index(
        "ix_projection_acknowledgements_acknowledged_at",
        "projection_acknowledgements",
        ["acknowledged_at"],
        unique=False,
    )
    op.create_index(
        "ix_projection_acknowledgements_entity_uuid",
        "projection_acknowledgements",
        ["entity_uuid", "projection_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_projection_acknowledgements_entity_uuid", table_name="projection_acknowledgements"
    )
    op.drop_index(
        "ix_projection_acknowledgements_acknowledged_at", table_name="projection_acknowledgements"
    )
    op.drop_table("projection_acknowledgements")
    op.drop_index("ix_change_events_created_at", table_name="change_events")
    op.drop_index("ix_change_events_aggregate_id_version", table_name="change_events")
    op.drop_table("change_events")
    op.drop_index("ix_projection_tasks_status_not_before_priority", table_name="projection_tasks")
    op.drop_index(
        "ix_projection_tasks_lease_expires_at",
        table_name="projection_tasks",
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.drop_index("ix_projection_tasks_entity_uuid", table_name="projection_tasks")
    op.drop_index(
        "ix_projection_tasks_claimable_order",
        table_name="projection_tasks",
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )
    op.drop_table("projection_tasks")
    op.drop_index("ix_normalized_artifacts_raw_sha256", table_name="normalized_artifacts")
    op.drop_index(
        "ix_normalized_artifacts_entity_uuid_produced_at", table_name="normalized_artifacts"
    )
    op.drop_table("normalized_artifacts")
    op.drop_index("ix_raw_objects_first_seen_at", table_name="raw_objects")
    op.drop_table("raw_objects")
    op.drop_index("ix_parse_attempts_raw_sha256", table_name="parse_attempts")
    op.drop_index("ix_parse_attempts_fetch_id", table_name="parse_attempts")
    op.drop_index("ix_parse_attempts_created_at", table_name="parse_attempts")
    op.drop_table("parse_attempts")
    op.drop_index(
        "ix_outbox_events_unpublished",
        table_name="outbox_events",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_index("ix_outbox_events_published_at_available_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_aggregate_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    # DETACH перед DROP: рядки, що осіли в DEFAULT, лишаються у відчепленій таблиці.
    op.execute(f"ALTER TABLE fetches DETACH PARTITION {FETCHES_DEFAULT_PARTITION}")
    op.drop_index("ix_fetches_source_id_fetched_at", table_name="fetches")
    op.drop_index("ix_fetches_raw_sha256", table_name="fetches")
    op.drop_index("ix_fetches_job_id", table_name="fetches")
    op.drop_table("fetches")
    op.drop_index("ix_entity_index_updated_at", table_name="entity_index")
    op.drop_index("ix_entity_index_domain_confirmed_version", table_name="entity_index")
    op.drop_table("entity_index")
    op.drop_index(
        "ix_artifact_upload_claims_orphan_candidates",
        table_name="artifact_upload_claims",
        postgresql_where=sa.text("status <> 'committed'"),
    )
    op.drop_index(
        "ix_artifact_upload_claims_lease_expires_at",
        table_name="artifact_upload_claims",
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.drop_table("artifact_upload_claims")
