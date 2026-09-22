"""Місячні партиції: імена, межі, перехід через рік, фільтр Alembic, захист назв."""

from __future__ import annotations

from datetime import date

import pytest

from collector.persistence.postgres.partitions import (
    MonthPartition,
    is_partition_child_name,
    month_partitions,
)


def test_month_partitions_roll_over_year_boundary() -> None:
    parts = month_partitions("audit_log", date(2026, 11, 1), months_ahead=2)
    assert [p.name for p in parts] == [
        "audit_log_y2026m11",
        "audit_log_y2026m12",
        "audit_log_y2027m01",
    ]
    assert parts[1].upper == date(2027, 1, 1)
    assert parts[2].create_sql == (
        "CREATE TABLE IF NOT EXISTS audit_log_y2027m01 PARTITION OF audit_log "
        "FOR VALUES FROM ('2027-01-01') TO ('2027-02-01')"
    )


def test_zero_months_ahead_gives_current_month_only() -> None:
    assert len(month_partitions("audit_log", date(2026, 9, 1), 0)) == 1
    with pytest.raises(ValueError, match="months_ahead"):
        month_partitions("audit_log", date(2026, 9, 1), -1)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("audit_log_y2026m09", True),
        ("audit_log", False),
        ("audit_log_y2026m9", False),
        ("crawl_jobs_y2026m09", False),  # не партиційована в PR1
        ("audit_log_y2026m09_extra", False),
    ],
)
def test_is_partition_child_name(name: str, expected: bool) -> None:
    assert is_partition_child_name(name) is expected


def test_create_sql_rejects_unsafe_table_identifier() -> None:
    with pytest.raises(ValueError, match="недопустима назва"):
        _ = MonthPartition("audit_log; DROP TABLE x", 2026, 9).create_sql
