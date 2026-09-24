"""WP-01A PR3a: лічильник видач outbox (N-2) і індекс validators conditional GET.

1. `outbox_events.delivery_attempts` (int, default 0, CHECK >= 0) — скільки разів рядок
   видано publisher-у (`outbox.fetch_unpublished`). Рядок, що досяг межі, паркується в тій
   самій lease-транзакції, тому crash/OOM publisher-а до `mark_failed` не обходить межу
   (N-2, варіант 2 — рішення оркестратора WP-01B). `attempts` (помилки `mark_failed`) і
   `delivery_attempts` (видачі) — окремі лічильники.
2. `fetches.requested_url_md5` — `GENERATED ALWAYS AS (md5(requested_url)) STORED` і partial
   index `ix_fetches_validators (source_id, requested_url_md5, fetched_at) WHERE outcome =
   'success' AND http_status IN (200, 206)` під `artifacts.latest_validators` (WP-02 п.2).
   `requested_url` — необмежений TEXT, тож у btree іде хеш; md5 тут — лише ключ індексу, не
   захист: запит додатково порівнює сам `requested_url`, колізія не дає хибного результату.
   `sha256()` тут непридатний як generated expression: `convert_to(text, 'UTF8')` у
   PostgreSQL `STABLE`, а generated column/index вимагають `IMMUTABLE`; `md5(text)` —
   `IMMUTABLE`. Колонка й індекс на партиціонованій таблиці поширюються на всі партиції,
   включно з `fetches_default` і тими, що `ensure_month_partitions` створить пізніше.

Downgrade безпечний для dev/test: прибирає індекс і дві колонки (похідні дані, немає
втрати джерельних фактів — `delivery_attempts` лише лічильник доставок).

Revision ID: 0006_queue_outbox_preflight
Revises: 0005_entity_version_guard
Create Date: 2026-09-24 12:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_queue_outbox_preflight"
down_revision: str | Sequence[str] | None = "0005_entity_version_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("delivery_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_delivery_attempts_non_negative"),
        "outbox_events",
        "delivery_attempts >= 0",
    )
    op.add_column(
        "fetches",
        sa.Column(
            "requested_url_md5",
            sa.String(length=32),
            sa.Computed("md5(requested_url)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_fetches_validators",
        "fetches",
        ["source_id", "requested_url_md5", "fetched_at"],
        unique=False,
        postgresql_where=sa.text("outcome = 'success' AND http_status IN (200, 206)"),
    )


def downgrade() -> None:
    op.drop_index("ix_fetches_validators", table_name="fetches")
    op.drop_column("fetches", "requested_url_md5")
    op.drop_constraint(
        op.f("ck_outbox_events_delivery_attempts_non_negative"), "outbox_events", type_="check"
    )
    op.drop_column("outbox_events", "delivery_attempts")
