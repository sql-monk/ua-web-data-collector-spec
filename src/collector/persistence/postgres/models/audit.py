"""`audit_log` — append-only журнал mutating actions (§13), партиційований по місяцях.

- PK `(audit_id, created_at)` — партиційна таблиця вимагає partition key у PK;
  `audit_id` — DB default `gen_random_uuid()` (єдиний дозволений випадок DB-генерації PK);
- `before_state`/`after_state` — JSONB control extension (before/after snapshot ресурсу для
  impact preview §13), не domain payload;
- append-only: тригер `audit_log_append_only` відхиляє UPDATE/DELETE (міграція), плюс
  runtime-ролі не мають UPDATE/DELETE grant (`sql/roles.sql`);
- партиції `audit_log_yYYYYmMM` створює `partitions.ensure_month_partitions` (CLI `db migrate`
  після upgrade і maintenance WP-12); autogenerate ігнорує child-таблиці (`env.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from collector.persistence.postgres.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_resource", "resource_type", "resource_id", "created_at"),
        Index("ix_audit_log_idempotency_key", "idempotency_key"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    audit_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=text("now()")
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(512))
