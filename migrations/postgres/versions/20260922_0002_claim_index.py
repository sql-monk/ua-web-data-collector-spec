"""WP-01A PR1 (gate 2, I-2): partial index під hot path `claim` (§7.2).

`ix_crawl_jobs_status_not_before_priority` (обов'язковий за карткою/R-32) не обслуговує
`ORDER BY priority DESC, not_before, job_id`: `status IN ('pending','retry')` на провідній
колонці не дає читати index у потрібному порядку, тож планувальник сортує всю чергу.
Новий partial index містить лише claimable-рядки і саме в порядку claim.

Вимір на 400 000 pending jobs (PostgreSQL 18, той самий запит, LIMIT 3):
до — Seq Scan + external merge sort, 331 мс; після — Index Scan, 0.18 мс.

Обидва indexes лишаються: перший — операторські вибірки за (status, not_before),
другий — claim. Ціна: ще один index на найгарячішій таблиці (лише по claimable-рядках,
тобто зникає разом із переходом job у succeeded/quarantined).

Revision ID: 0002_claim_index
Revises: 0001_control_queue
Create Date: 2026-09-22 14:46 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_claim_index"
down_revision: str | Sequence[str] | None = "0001_control_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_crawl_jobs_claimable_order",
        "crawl_jobs",
        [sa.literal_column("priority DESC"), "not_before", "job_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crawl_jobs_claimable_order",
        table_name="crawl_jobs",
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )
