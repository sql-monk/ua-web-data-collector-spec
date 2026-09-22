"""WP-01A PR1 (gate 3): DEFAULT-партиція `audit_log` (M-5) і намір drain (M-2).

M-5: `audit_log` створювалась партиційованою **без жодної партиції** — після чистого
`alembic upgrade head` (перший крок CI і команда перевірки картки) перший `append_audit` падав
з `no partition of relation "audit_log" found for row`. Оскільки `request_scale` пише audit у
тій самій транзакції, що й desired state, пропущене обслуговування зупиняло б **усі** audited
дії control plane. DEFAULT-партиція приймає рядки місяців, для яких місячної партиції ще немає:
журнал не втрачається, а ненульова кількість рядків у ній — сигнал «партиції відстають»
(`partitions.default_partition_row_count`; метрика §14.1 і алерт §14.2 — owner WP-12).

Наслідок для обслуговування: поки в DEFAULT є рядки місяця M, `CREATE TABLE … PARTITION OF …
FOR VALUES FROM (M) TO (M+1)` відмовить — maintenance має спершу перенести їх (процедура — у
docstring `partitions.default_partition_row_count`).

M-2: `worker_instances.drain_requested_at` зберігає намір drain окремо від `status`, бо пара
переходів `draining → stale → ready` (instance відновив heartbeat після паузи) мовчки
скасовувала drain, ініційований scale-командою.

Revision ID: 0003_default_partition
Revises: 0002_claim_index
Create Date: 2026-09-22 15:24 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from collector.persistence.postgres.partitions import (
    default_partition_name,
    default_partition_sql,
)

revision: str = "0003_default_partition"
down_revision: str | Sequence[str] | None = "0002_claim_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(default_partition_sql("audit_log"))
    op.add_column(
        "worker_instances",
        sa.Column("drain_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("worker_instances", "drain_requested_at")
    # DETACH перед DROP: рядки, що осіли в DEFAULT, лишаються у відчепленій таблиці, а не
    # зникають разом із партицією (downgrade — дев-сценарій, але дані журналу не губимо).
    op.execute(f"ALTER TABLE audit_log DETACH PARTITION {default_partition_name('audit_log')}")
