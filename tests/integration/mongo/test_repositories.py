"""WP-01B PR1 п.7: async-репозиторії над PyMongo Async API проти RS (concerns §8)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from mongo_factories import at, current_document, receipt
from pymongo import MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.database import Database

from collector.contracts import new_entity_id
from collector.persistence.mongo import repositories
from collector.persistence.mongo.admin import apply_mongo_schema
from collector.persistence.mongo.repositories import receipt_to_document
from collector.persistence.mongo.schema import (
    APPLIED_PROJECTION_RECEIPTS,
    CATALOG_ITEMS_CURRENT,
    ENTITY_PROJECTION_VERSIONS,
)

pytestmark = pytest.mark.integration

ADb = AsyncDatabase[dict[str, Any]]


@pytest.fixture
def schema(mongo_root: MongoClient[dict[str, Any]], mongo_db: Database[dict[str, Any]]) -> None:
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True)


@pytest.mark.usefixtures("schema")
async def test_get_current_and_exact_version(mongo_async_db: ADb) -> None:
    entity = new_entity_id()
    doc = current_document(entity, item="r1", version=2)
    await mongo_async_db[CATALOG_ITEMS_CURRENT].insert_one(doc)
    for version in (1, 2):
        await mongo_async_db[ENTITY_PROJECTION_VERSIONS].insert_one(
            {"entity_uuid": entity, "projection_version": version, "projection_task_id": uuid4()}
        )
    current = await repositories.get_current(mongo_async_db, CATALOG_ITEMS_CURRENT, entity)
    assert current is not None
    assert current["_id"] == entity and isinstance(current["_id"], UUID)
    assert current["last_seen_at"].tzinfo is not None  # tz_aware
    assert await repositories.get_current(mongo_async_db, CATALOG_ITEMS_CURRENT, uuid4()) is None
    exact = await repositories.get_exact_version(mongo_async_db, entity, 1)
    assert exact is not None and exact["projection_version"] == 1
    assert await repositories.get_exact_version(mongo_async_db, entity, 3) is None


@pytest.mark.usefixtures("schema")
async def test_get_receipt_round_trips_event_bytes(mongo_async_db: ADb) -> None:
    original = receipt(version=4)
    await mongo_async_db[APPLIED_PROJECTION_RECEIPTS].insert_one(receipt_to_document(original))
    raw = await mongo_async_db[APPLIED_PROJECTION_RECEIPTS].find_one(
        {"_id": original.projection_task_id}
    )
    assert raw is not None
    assert isinstance(raw["event_bytes"], bytes)  # BSON Binary, не base64-рядок
    loaded = await repositories.get_receipt(mongo_async_db, original.projection_task_id)
    assert loaded == original
    assert loaded is not None and loaded.event_bytes == original.event_bytes
    assert await repositories.get_receipt(mongo_async_db, uuid4()) is None


@pytest.mark.usefixtures("schema")
async def test_get_receipt_reads_inside_transaction(mongo_async_db: ADb) -> None:
    original = receipt(applied=False, changed=False)
    async with mongo_async_db.client.start_session() as session:
        async with await session.start_transaction():
            await mongo_async_db[APPLIED_PROJECTION_RECEIPTS].insert_one(
                receipt_to_document(original), session=session
            )
            inside = await repositories.get_receipt(
                mongo_async_db, original.projection_task_id, session=session
            )
            outside = await repositories.get_receipt(mongo_async_db, original.projection_task_id)
            assert inside == original
            assert outside is None  # snapshot-ізоляція до commit
    assert await repositories.get_receipt(mongo_async_db, original.projection_task_id) == original


@pytest.mark.usefixtures("schema")
@pytest.mark.parametrize("page_size", [1, 2, 3, 7])
async def test_list_receipts_keyset_has_no_duplicates_or_gaps(
    mongo_async_db: ADb, page_size: int
) -> None:
    """Однакові `committed_at` на межі сторінки: tie-break за `_id`, без `skip` (§15)."""
    minutes = [0, 1, 1, 1, 2, 2, 5]
    receipts = [receipt(committed_at=at(m), version=i + 1) for i, m in enumerate(minutes)]
    await mongo_async_db[APPLIED_PROJECTION_RECEIPTS].insert_many(
        [receipt_to_document(r) for r in reversed(receipts)]
    )
    expected = sorted(receipts, key=lambda r: (r.committed_at, r.projection_task_id.bytes))
    seen = []
    after = None
    for _ in range(len(receipts) + 1):
        page = await repositories.list_receipts(mongo_async_db, after=after, limit=page_size)
        seen.extend(page.items)
        if page.next_after is None:
            break
        after = page.next_after
    assert page.next_after is None
    assert [r.projection_task_id for r in seen] == [r.projection_task_id for r in expected]
