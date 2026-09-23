"""Інваріанти моделей без БД (R-27, R-32, картка «Спільні вимоги»)."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, LargeBinary, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from collector.contracts.enums import RouteState, SourceState
from collector.persistence.postgres.models import Base
from collector.persistence.postgres.models.queue import CLAIMABLE_JOB_STATUSES, CLAIMABLE_PREDICATE
from collector.persistence.postgres.partitions import PARTITIONED_TABLES
from collector.workers.roles import WorkerRole

EXPECTED_TABLES = {
    "sources",
    "source_policy_versions",
    "source_routes",
    "source_cursors",
    "crawl_runs",
    "crawl_jobs",
    "origin_rate_buckets",
    "origin_rate_permits",
    "worker_pools",
    "worker_instances",
    "scale_commands",
    "dead_letters",
    "audit_log",
}
PR2_TABLES = {
    "fetches",
    "raw_objects",
    "parse_attempts",
    "artifact_upload_claims",
    "normalized_artifacts",
    "projection_tasks",
    "projection_acknowledgements",
    "entity_index",
    "change_events",
    "outbox_events",
}
PARTITION_KEYS = {"audit_log": "created_at", "fetches": "fetched_at"}
ALLOWED_JSONB = {
    ("crawl_jobs", "args"),
    ("audit_log", "before_state"),
    ("audit_log", "after_state"),
}
MANDATORY_INDEXES = {
    "crawl_jobs": {("status", "not_before", "priority", "job_id"), ("lease_expires_at",)},
    "origin_rate_permits": {("origin", "lease_expires_at")},
    "worker_instances": {("role", "status", "last_heartbeat_at")},
    "audit_log": {("created_at",)},
    # §9.1 обов'язкові operational indexes PR2.
    "projection_tasks": {("status", "not_before", "priority", "task_id")},
    "outbox_events": {("published_at", "available_at", "event_id")},
    "entity_index": {("domain", "confirmed_projection_version", "entity_uuid")},
}


def _checks(table_name: str) -> list[str]:
    table = Base.metadata.tables[table_name]
    return [str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)]


def test_pr1_and_pr2_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES | PR2_TABLES


def test_pr2_unique_keys_that_carry_idempotency() -> None:
    """Unique-ключі, на яких тримається ідемпотентність потоку §7.3 (R-36, R-41, R-42)."""

    def uniques(name: str) -> set[tuple[str, ...]]:
        table = Base.metadata.tables[name]
        return {
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }

    assert ("entity_uuid", "projection_version") in uniques("projection_tasks")
    # CR-1: ідемпотентність — за ідентичністю parse-кроку, не за artifact.
    assert ("parse_key",) in uniques("projection_tasks")
    assert ("artifact_id", "target_collection") not in uniques("projection_tasks")
    assert ("object_key",) in uniques("artifact_upload_claims")
    assert ("object_key",) in uniques("normalized_artifacts")
    assert ("sha256",) in uniques("raw_objects")
    assert ("event_id",) in uniques("outbox_events")
    assert ("event_id",) in uniques("change_events")
    assert ("source_id", "source_item_id") in uniques("entity_index")
    acks = Base.metadata.tables["projection_acknowledgements"]
    assert [c.name for c in acks.primary_key.columns] == ["task_id"]


def test_pr2_tables_carry_no_payload_beyond_bounded_event_bytes() -> None:
    """R-27: жодного domain payload — лише pointer/hash/size; `bytea` дозволено тільки для
    готових event bytes (§7.3, ≤ 256 KiB, CHECK `inline_size`)."""
    binary = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, LargeBinary)
    }
    assert binary == {("outbox_events", "payload_bytes"), ("change_events", "event_bytes")}
    for name in ("outbox_events", "change_events"):
        assert any("262144" in check for check in _checks(name)), name


def test_no_domain_jsonb_and_all_timestamps_are_timestamptz() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSONB):
                assert (table.name, column.name) in ALLOWED_JSONB
            if isinstance(column.type, DateTime):
                assert column.type.timezone, f"{table.name}.{column.name}"


def test_mandatory_indexes_present() -> None:
    for table_name, expected in MANDATORY_INDEXES.items():
        table = Base.metadata.tables[table_name]
        actual = {tuple(c.name for c in ix.columns) for ix in table.indexes}
        assert expected <= actual, table_name
    crawl_jobs = Base.metadata.tables["crawl_jobs"]
    claim_ix = next(ix for ix in crawl_jobs.indexes if ix.name == "ix_crawl_jobs_claimable_order")
    # I-2: порядок колонок index-у має дослівно збігатися з ORDER BY у `claim`.
    assert [str(e).removeprefix("crawl_jobs.") for e in claim_ix.expressions] == [
        "priority DESC",
        "not_before",
        "job_id",
    ]
    assert str(claim_ix.dialect_options["postgresql"]["where"]) == CLAIMABLE_PREDICATE
    lease_ix = next(ix for ix in crawl_jobs.indexes if ix.name == "ix_crawl_jobs_lease_expires_at")
    assert "status = 'leased'" in str(lease_ix.dialect_options["postgresql"]["where"])
    assert "uq_crawl_jobs_idempotency_key" in {c.name for c in crawl_jobs.constraints}
    runs = Base.metadata.tables["crawl_runs"]
    running_full = next(ix for ix in runs.indexes if ix.name == "uq_crawl_runs_running_full")
    assert running_full.unique


def test_enum_checks_use_shared_contract_values() -> None:
    assert any(all(s.value in c for s in SourceState) for c in _checks("sources"))
    assert any(all(s.value in c for s in RouteState) for c in _checks("source_routes"))
    assert any(all(r.value in c for r in WorkerRole) for c in _checks("worker_pools"))
    assert any(
        all(s in c for s in ("pending", "leased", "succeeded", "retry", "quarantined"))
        for c in _checks("crawl_jobs")
    )


def test_partitioned_tables_declare_partition_by_and_pk_includes_key() -> None:
    assert set(PARTITIONED_TABLES) == set(PARTITION_KEYS)
    for name in PARTITIONED_TABLES:
        table = Base.metadata.tables[name]
        key = PARTITION_KEYS[name]
        assert key in table.dialect_options["postgresql"]["partition_by"]
        assert key in {c.name for c in table.primary_key.columns}


def test_claimable_predicate_matches_statuses_used_by_claim() -> None:
    """Предикат partial index і статуси, які бере `claim`, не можуть розійтися (I-2)."""
    for status in CLAIMABLE_JOB_STATUSES:
        assert f"'{status}'" in CLAIMABLE_PREDICATE
    assert CLAIMABLE_PREDICATE.startswith("status IN (")
    assert "succeeded" not in CLAIMABLE_PREDICATE
    assert "quarantined" not in CLAIMABLE_PREDICATE
    assert "leased" not in CLAIMABLE_PREDICATE


def test_revision_columns_on_versioned_resources() -> None:
    for name in (
        "sources",
        "source_routes",
        "source_cursors",
        "worker_pools",
        "origin_rate_buckets",
    ):
        assert "revision" in Base.metadata.tables[name].columns, name
