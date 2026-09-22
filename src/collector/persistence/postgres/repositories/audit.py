"""Append-only audit log §13: `append_audit`.

Transaction boundary: викликач — audit-запис робиться в тій самій транзакції, що й mutating
action (scale command, source state, release publish), щоб не було дії без сліду і сліду без дії.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import JsonObject, new_entity_id
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.models import AuditLog


async def append_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    before: JsonObject | None = None,
    after: JsonObject | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> AuditLog:
    """Додає audit-запис; з `idempotency_key` повторний виклик повертає існуючий запис.

    Ідемпотентність — best effort через index `ix_audit_log_idempotency_key` (партиційована
    таблиця не дає глобального unique без partition key у ключі); одночасні дублі того самого
    ключа теоретично можливі й нешкідливі для журналу. UPDATE/DELETE відхиляє тригер
    `audit_log_append_only`.
    """
    if idempotency_key is not None:
        existing = await session.scalar(
            select(AuditLog)
            .where(AuditLog.idempotency_key == idempotency_key)
            .order_by(AuditLog.created_at)
            .limit(1)
        )
        if existing is not None:
            return existing
    entry = AuditLog(
        audit_id=new_entity_id(),
        created_at=resolve_now(now),
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_state=before,
        after_state=after,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_audit(session: AsyncSession, audit_id: UUID, created_at: datetime) -> AuditLog | None:
    """Запис за складеним PK (partition key обов'язковий для partition pruning)."""
    return await session.get(AuditLog, (audit_id, created_at))
