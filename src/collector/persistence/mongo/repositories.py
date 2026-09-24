"""Async-репозиторії domain-БД Mongo (PyMongo Async API, §8): читання current/exact/receipts.

Операції: `get_current`, `get_exact_version` (hot-частина §9.5; archive locator — PR4),
`get_receipt`, `list_receipts`; явний BSON mapping receipt-а — `receipt_to_document` /
`receipt_from_document` (без ODM, §8).

Transaction boundary — викликач (як у WP-01A): кожна функція приймає необов'язкову
`AsyncClientSession`; якщо сесія в транзакції, читання йде в її snapshot. Concerns (primary,
majority) задає клієнт (`client.create_client`), `maxTimeMS` — параметр виклику.

BSON mapping receipt-а: `_id = projection_task_id` (UUID, Binary subtype 4; §9.2 «PK
projection_task_id»), `event_bytes` — BSON Binary, datetime — BSON date (мілісекунди: мікросекунди
відкидаються драйвером, тож projector має фіксувати `committed_at` з точністю до мс, щоб replay
повертав той самий receipt), поля зі значенням None не зберігаються.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pymongo import ASCENDING
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from collector.contracts import AppliedProjectionReceipt
from collector.persistence.mongo.client import DEFAULT_MAX_TIME_MS
from collector.persistence.mongo.schema import (
    APPLIED_PROJECTION_RECEIPTS,
    CURRENT_COLLECTIONS,
    ENTITY_PROJECTION_VERSIONS,
)

Document = dict[str, Any]
ReceiptCursor = tuple[datetime, UUID]
"""Keyset-позиція `(committed_at, _id)` останнього прочитаного receipt-а (§15, без `skip`)."""

MAX_PAGE_SIZE = 1000


@dataclass(frozen=True, slots=True)
class ReceiptPage:
    """Сторінка receipts; `next_after` — None, якщо далі нічого немає."""

    items: list[AppliedProjectionReceipt]
    next_after: ReceiptCursor | None


def receipt_to_document(receipt: AppliedProjectionReceipt) -> Document:
    """Receipt → BSON-документ `applied_projection_receipts` (проходить validator 0001)."""
    body = receipt.model_dump(mode="python", exclude_none=True)
    return {"_id": receipt.projection_task_id, **body}


def receipt_from_document(document: Document) -> AppliedProjectionReceipt:
    """BSON-документ → `AppliedProjectionReceipt` (валідація контракту, `_id` відкидається)."""
    body = {key: value for key, value in document.items() if key != "_id"}
    return AppliedProjectionReceipt.model_validate(body)


def _require_current(collection: str) -> str:
    if collection not in CURRENT_COLLECTIONS:
        msg = f"{collection!r} не є current collection §9.2 ({', '.join(CURRENT_COLLECTIONS)})"
        raise ValueError(msg)
    return collection


async def get_current(
    db: AsyncDatabase[Document],
    collection: str,
    entity_uuid: UUID,
    *,
    session: AsyncClientSession | None = None,
    max_time_ms: int = DEFAULT_MAX_TIME_MS,
) -> Document | None:
    """Current document сутності (`_id = entity_uuid`) або None."""
    return await db[_require_current(collection)].find_one(
        {"_id": entity_uuid}, session=session, max_time_ms=max_time_ms
    )


async def get_exact_version(
    db: AsyncDatabase[Document],
    entity_uuid: UUID,
    projection_version: int,
    *,
    session: AsyncClientSession | None = None,
    max_time_ms: int = DEFAULT_MAX_TIME_MS,
) -> Document | None:
    """Hot version record `{entity_uuid, projection_version}` або None (archive — PR4, §9.5)."""
    return await db[ENTITY_PROJECTION_VERSIONS].find_one(
        {"entity_uuid": entity_uuid, "projection_version": projection_version},
        session=session,
        max_time_ms=max_time_ms,
    )


async def get_receipt(
    db: AsyncDatabase[Document],
    projection_task_id: UUID,
    *,
    session: AsyncClientSession | None = None,
    max_time_ms: int = DEFAULT_MAX_TIME_MS,
) -> AppliedProjectionReceipt | None:
    """Receipt task-и (idempotency key — `projection_task_id`) або None."""
    document = await db[APPLIED_PROJECTION_RECEIPTS].find_one(
        {"_id": projection_task_id}, session=session, max_time_ms=max_time_ms
    )
    return None if document is None else receipt_from_document(document)


async def list_receipts(
    db: AsyncDatabase[Document],
    *,
    after: ReceiptCursor | None = None,
    limit: int = 100,
    session: AsyncClientSession | None = None,
    max_time_ms: int = DEFAULT_MAX_TIME_MS,
) -> ReceiptPage:
    """Receipts у порядку `(committed_at, _id)` після `after`; keyset без `skip` (§15).

    Сортування стабільне (tie-break за унікальним `_id`), тому межа сторінки не дублює і не
    пропускає записи з однаковим `committed_at`. Використовує index `ix_committed_cursor`.
    """
    if not 1 <= limit <= MAX_PAGE_SIZE:
        msg = f"limit має бути в межах 1..{MAX_PAGE_SIZE}"
        raise ValueError(msg)
    query: Document = {}
    if after is not None:
        committed_at, last_id = after
        query = {
            "$or": [
                {"committed_at": {"$gt": committed_at}},
                {"committed_at": committed_at, "_id": {"$gt": last_id}},
            ]
        }
    cursor = (
        db[APPLIED_PROJECTION_RECEIPTS]
        .find(query, session=session, max_time_ms=max_time_ms)
        .sort([("committed_at", ASCENDING), ("_id", ASCENDING)])
        .limit(limit + 1)
    )
    documents = await cursor.to_list()
    has_more = len(documents) > limit
    page = documents[:limit]
    next_after: ReceiptCursor | None = None
    if has_more and page:
        last = page[-1]
        next_after = (last["committed_at"], last["_id"])
    return ReceiptPage(items=[receipt_from_document(d) for d in page], next_after=next_after)
