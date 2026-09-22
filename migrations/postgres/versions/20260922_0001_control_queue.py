"""WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.

Таблиці §9.1: sources, source_policy_versions, source_routes, source_cursors, crawl_runs,
crawl_jobs, dead_letters, origin_rate_buckets, origin_rate_permits, worker_pools,
worker_instances, scale_commands, audit_log (RANGE-партиції по created_at; child-партиції
створює collector.persistence.postgres.partitions, не міграція).

Обов'язкові indexes (картка PR1): crawl_jobs(status, not_before, priority, job_id);
unique crawl_jobs(idempotency_key); crawl_jobs(lease_expires_at) WHERE status='leased';
origin_rate_permits(origin, lease_expires_at); worker_instances(role, status,
last_heartbeat_at); audit_log(created_at) — на партиційованій таблиці (успадковується
кожною партицією). FR-002: unique crawl_runs(source_id) WHERE status='running' AND kind='full'.

audit_log append-only: тригер audit_log_append_only відхиляє UPDATE/DELETE.

Downgrade реалізовано (початкова ревізія — повне видалення схеми; для production
forward-only, див. картку «Rollback/disable»).

Revision ID: 0001_control_queue
Revises:
Create Date: 2026-09-22 13:26:10 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_control_queue"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_LOG_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % denied', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$
"""


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column(
            "audit_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=False),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("audit_id", "created_at", name=op.f("pk_audit_log")),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"], unique=False)
    op.create_index("ix_audit_log_idempotency_key", "audit_log", ["idempotency_key"], unique=False)
    op.create_index(
        "ix_audit_log_resource",
        "audit_log",
        ["resource_type", "resource_id", "created_at"],
        unique=False,
    )
    op.execute(AUDIT_LOG_APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()"
    )
    op.create_table(
        "origin_rate_buckets",
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("capacity_tokens", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("refill_per_second", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("available_tokens", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("last_refill_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_reason", sa.String(length=256), nullable=True),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
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
            "available_tokens >= 0 AND available_tokens <= capacity_tokens",
            name=op.f("ck_origin_rate_buckets_tokens_range"),
        ),
        sa.CheckConstraint(
            "capacity_tokens > 0 AND refill_per_second > 0",
            name=op.f("ck_origin_rate_buckets_positive_rate"),
        ),
        sa.CheckConstraint("max_concurrency >= 0", name=op.f("ck_origin_rate_buckets_concurrency")),
        sa.PrimaryKeyConstraint("origin", name=op.f("pk_origin_rate_buckets")),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="paused", nullable=False),
        sa.Column("state_reason", sa.String(length=512), nullable=True),
        sa.Column("current_policy_version_id", sa.Uuid(), nullable=True),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
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
            "domain IN ('news', 'vehicle', 'catalog')", name=op.f("ck_sources_domain")
        ),
        sa.CheckConstraint(
            "state IN ('enabled', 'paused', 'disabled', 'blocked_anonymous')",
            name=op.f("ck_sources_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("source_id", name=op.f("uq_sources_source_id")),
    )
    op.create_table(
        "worker_pools",
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("desired_replicas", sa.Integer(), nullable=False),
        sa.Column("desired_concurrency", sa.Integer(), nullable=False),
        sa.Column("min_replicas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_replicas", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default="manual", nullable=False),
        sa.Column(
            "resource_profile", sa.String(length=64), server_default="default", nullable=False
        ),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("update_reason", sa.String(length=512), nullable=True),
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
        sa.CheckConstraint("mode IN ('manual', 'autoscale')", name=op.f("ck_worker_pools_mode")),
        sa.CheckConstraint(
            "role IN ('discovery', 'fetch', 'browser', 'parse', 'projector', 'translation', "
            "'export', 'maintenance')",
            name=op.f("ck_worker_pools_role"),
        ),
        sa.CheckConstraint("desired_concurrency >= 1", name=op.f("ck_worker_pools_concurrency")),
        sa.CheckConstraint(
            "min_replicas >= 0 AND max_replicas >= min_replicas "
            "AND desired_replicas BETWEEN min_replicas AND max_replicas",
            name=op.f("ck_worker_pools_replicas_range"),
        ),
        sa.PrimaryKeyConstraint("role", name=op.f("pk_worker_pools")),
    )
    op.create_table(
        "scale_commands",
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("expected_pool_revision", sa.BigInteger(), nullable=False),
        sa.Column("applied_pool_revision", sa.BigInteger(), nullable=False),
        sa.Column("requested_replicas", sa.Integer(), nullable=False),
        sa.Column("requested_concurrency", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="requested", nullable=False),
        sa.Column("cli_command", sa.String(length=1024), nullable=True),
        sa.Column("result", sa.String(length=2048), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("audit_created_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('requested', 'draining', 'awaiting_manual_apply', 'applying', "
            "'applied', 'failed', 'superseded')",
            name=op.f("ck_scale_commands_status"),
        ),
        sa.CheckConstraint(
            "requested_replicas >= 0 AND requested_concurrency >= 1",
            name=op.f("ck_scale_commands_requested_values"),
        ),
        sa.ForeignKeyConstraint(
            ["role"],
            ["worker_pools.role"],
            name=op.f("fk_scale_commands_role_worker_pools"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("command_id", name=op.f("pk_scale_commands")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_scale_commands_idempotency_key")),
    )
    op.create_index(
        "ix_scale_commands_role_status_created_at",
        "scale_commands",
        ["role", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "source_policy_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("requests_per_second", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("burst_tokens", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("crawl_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("browser_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("robots_policy", sa.String(length=32), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_uri", sa.String(length=1024), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_source_policy_versions_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_policy_versions")),
        sa.UniqueConstraint(
            "source_id", "version", name=op.f("uq_source_policy_versions_source_id_version")
        ),
    )
    # Циклічний FK sources -> source_policy_versions: після створення обох таблиць.
    op.create_foreign_key(
        op.f("fk_sources_current_policy_version_id_source_policy_versions"),
        "sources",
        "source_policy_versions",
        ["current_policy_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "source_routes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("route_kind", sa.String(length=16), nullable=False),
        sa.Column("route_key", sa.String(length=1024), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="healthy", nullable=False),
        sa.Column("state_reason", sa.String(length=512), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("circuit_open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
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
            "route_kind IN ('rss', 'sitemap', 'category', 'detail', 'api', 'browser')",
            name=op.f("ck_source_routes_route_kind"),
        ),
        sa.CheckConstraint(
            "state IN ('healthy', 'degraded', 'circuit_open', 'unsupported')",
            name=op.f("ck_source_routes_state"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_source_routes_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_routes")),
        sa.UniqueConstraint(
            "source_id",
            "route_kind",
            "route_key",
            name=op.f("uq_source_routes_source_id_route_kind_route_key"),
        ),
    )
    op.create_index(
        "ix_source_routes_state", "source_routes", ["state", "circuit_open_until"], unique=False
    )
    op.create_table(
        "worker_instances",
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="starting", nullable=False),
        sa.Column("deployment", sa.String(length=128), nullable=True),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("hostname", sa.String(length=256), nullable=True),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("slots_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("slots_active", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("active_leases", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pool_revision", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('starting', 'ready', 'draining', 'stopped', 'stale')",
            name=op.f("ck_worker_instances_status"),
        ),
        sa.CheckConstraint(
            "slots_total >= 0 AND slots_active >= 0 AND active_leases >= 0",
            name=op.f("ck_worker_instances_slots"),
        ),
        sa.ForeignKeyConstraint(
            ["role"],
            ["worker_pools.role"],
            name=op.f("fk_worker_instances_role_worker_pools"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_worker_instances")),
    )
    op.create_index(
        "ix_worker_instances_role_status_last_heartbeat_at",
        "worker_instances",
        ["role", "status", "last_heartbeat_at"],
        unique=False,
    )
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("started_by", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
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
            "kind IN ('full', 'incremental', 'replay', 'backfill')", name=op.f("ck_crawl_runs_kind")
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_crawl_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["source_policy_versions.id"],
            name=op.f("fk_crawl_runs_policy_version_id_source_policy_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_crawl_runs_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_runs")),
    )
    op.create_index(
        "ix_crawl_runs_source_id_started_at",
        "crawl_runs",
        ["source_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "uq_crawl_runs_running_full",
        "crawl_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND kind = 'full'"),
    )
    op.create_table(
        "source_cursors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("route_id", sa.Uuid(), nullable=True),
        sa.Column("cursor_kind", sa.String(length=32), nullable=False),
        sa.Column("cursor_key", sa.String(length=1024), nullable=False),
        sa.Column("cursor_value", sa.String(length=4096), nullable=False),
        sa.Column("cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["route_id"],
            ["source_routes.id"],
            name=op.f("fk_source_cursors_route_id_source_routes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_source_cursors_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_cursors")),
        sa.UniqueConstraint(
            "source_id",
            "cursor_kind",
            "cursor_key",
            name=op.f("uq_source_cursors_source_id_cursor_kind_cursor_key"),
        ),
    )
    op.create_table(
        "crawl_jobs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("100"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column(
            "args",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
            name=op.f("ck_crawl_jobs_lease_consistent"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', 'retry', 'quarantined')",
            name=op.f("ck_crawl_jobs_status"),
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts >= 1", name=op.f("ck_crawl_jobs_attempts")
        ),
        sa.CheckConstraint(
            "octet_length(args::text) <= 8192", name=op.f("ck_crawl_jobs_args_size")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_crawl_jobs_run_id_crawl_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_crawl_jobs_source_id_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_crawl_jobs")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_crawl_jobs_idempotency_key")),
    )
    op.create_index(
        "ix_crawl_jobs_lease_expires_at",
        "crawl_jobs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.create_index("ix_crawl_jobs_run_id", "crawl_jobs", ["run_id"], unique=False)
    op.create_index(
        "ix_crawl_jobs_status_not_before_priority",
        "crawl_jobs",
        ["status", "not_before", "priority", "job_id"],
        unique=False,
    )
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("resolution", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "reason IN ('max_attempts', 'quarantine')", name=op.f("ck_dead_letters_reason")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["crawl_jobs.job_id"],
            name=op.f("fk_dead_letters_job_id_crawl_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dead_letters")),
    )
    op.create_index("ix_dead_letters_created_at", "dead_letters", ["created_at"], unique=False)
    op.create_index("ix_dead_letters_job_id", "dead_letters", ["job_id"], unique=False)
    op.create_table(
        "origin_rate_permits",
        sa.Column("permit_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("owner_instance", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "release_reason IN ('released', 'expired')",
            name=op.f("ck_origin_rate_permits_release_reason"),
        ),
        sa.CheckConstraint(
            "(released_at IS NULL) = (release_reason IS NULL)",
            name=op.f("ck_origin_rate_permits_release_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["crawl_jobs.job_id"],
            name=op.f("fk_origin_rate_permits_job_id_crawl_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["origin"],
            ["origin_rate_buckets.origin"],
            name=op.f("fk_origin_rate_permits_origin_origin_rate_buckets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("permit_id", name=op.f("pk_origin_rate_permits")),
    )
    op.create_index(
        "ix_origin_rate_permits_origin_lease_expires_at",
        "origin_rate_permits",
        ["origin", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_origin_rate_permits_origin_lease_expires_at", table_name="origin_rate_permits"
    )
    op.drop_table("origin_rate_permits")
    op.drop_index("ix_dead_letters_job_id", table_name="dead_letters")
    op.drop_index("ix_dead_letters_created_at", table_name="dead_letters")
    op.drop_table("dead_letters")
    op.drop_index("ix_crawl_jobs_status_not_before_priority", table_name="crawl_jobs")
    op.drop_index("ix_crawl_jobs_run_id", table_name="crawl_jobs")
    op.drop_index(
        "ix_crawl_jobs_lease_expires_at",
        table_name="crawl_jobs",
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.drop_table("crawl_jobs")
    op.drop_table("source_cursors")
    op.drop_index(
        "uq_crawl_runs_running_full",
        table_name="crawl_runs",
        postgresql_where=sa.text("status = 'running' AND kind = 'full'"),
    )
    op.drop_index("ix_crawl_runs_source_id_started_at", table_name="crawl_runs")
    op.drop_table("crawl_runs")
    op.drop_index(
        "ix_worker_instances_role_status_last_heartbeat_at", table_name="worker_instances"
    )
    op.drop_table("worker_instances")
    op.drop_index("ix_source_routes_state", table_name="source_routes")
    op.drop_table("source_routes")
    op.drop_constraint(
        op.f("fk_sources_current_policy_version_id_source_policy_versions"),
        "sources",
        type_="foreignkey",
    )
    op.drop_table("source_policy_versions")
    op.drop_index("ix_scale_commands_role_status_created_at", table_name="scale_commands")
    op.drop_table("scale_commands")
    op.drop_table("worker_pools")
    op.drop_table("sources")
    op.drop_table("origin_rate_buckets")
    op.drop_index("ix_audit_log_resource", table_name="audit_log")
    op.drop_index("ix_audit_log_idempotency_key", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
