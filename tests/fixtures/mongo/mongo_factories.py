"""Синтетичні BSON-документи для тестів Mongo-схеми WP-01B.

provenance: synthetic, WP-01B — згенеровано фабрикою з контрактів WP-01C
(`CurrentDocumentBase`, `AppliedProjectionReceipt`), без даних реальних джерел і без контактів.
`source_id` — ідентифікатор із реєстру джерел (контракт валідує його), значення item/URL вигадані.

Імпортується як `mongo_factories` (каталог додає в sys.path conftest тестів: pytest працює в
`--import-mode=importlib` без пакета `tests`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from collector.contracts import (
    AppliedProjectionReceipt,
    DomainChangedEvent,
    encode_event,
    new_entity_id,
)
from collector.contracts.current import CurrentDocumentBase, compute_state_hash_v1
from collector.contracts.enums import EntityKind
from collector.persistence.mongo.repositories import receipt_to_document

PROVENANCE = "synthetic, WP-01B"
T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
SOURCE_ID = "catalog_ua_rozetka"
RAW_SHA = "b" * 64


def state_hash(n: int) -> str:
    return f"v1:{n:064x}"


def current_document(
    entity_uuid: UUID | None = None,
    *,
    item: str = "synthetic-item-1",
    version: int = 1,
    **extra: Any,
) -> dict[str, Any]:
    """Валідний current document (BSON-форма: UUID/datetime як об'єкти, `_id` = entity UUID)."""
    core = {"title": "Синтетичний товар", "brand": "Example"}
    attributes = {"power_w": 600}
    latest_state = {"status": "active", "version": version}
    model = CurrentDocumentBase.model_validate(
        {
            "_id": str(entity_uuid or new_entity_id()),
            "entity_kind": EntityKind.CATALOG_ITEM.value,
            "source": {
                "source_id": SOURCE_ID,
                "source_item_id": item,
                "canonical_url": f"https://example.invalid/items/{item}",
            },
            "identity_hash": "v1:" + "c" * 64,
            "projection_version": version,
            "state_hash": compute_state_hash_v1(core, attributes, latest_state),
            "core": core,
            "attributes": attributes,
            "latest_state": latest_state,
            "lineage": {
                "fetch_id": str(uuid4()),
                "raw_sha256": RAW_SHA,
                "parser_version": "catalog-parser/1.0.0",
                "projection_task_id": str(uuid4()),
            },
            "time": {
                "observed_at": "2026-09-01T11:59:00Z",
                "fetched_at": "2026-09-01T11:59:30Z",
                "ingested_at": "2026-09-01T12:00:00Z",
                "source_time_precision": "minute",
            },
            "first_seen_at": "2026-08-01T00:00:00Z",
            "last_seen_at": "2026-09-01T12:00:00Z",
        }
    )
    document = model.model_dump(mode="python", by_alias=True)
    document.update(extra)
    return document


def receipt(
    *,
    task_id: UUID | None = None,
    entity_uuid: UUID | None = None,
    version: int = 1,
    applied: bool = True,
    changed: bool = True,
    committed_at: datetime = T0,
    payload_size: int = 0,
) -> AppliedProjectionReceipt:
    """Receipt; для `applied AND changed` — з готовими event bytes (`encode_event`)."""
    task = task_id or uuid4()
    entity = entity_uuid or new_entity_id()
    fields: dict[str, Any] = {}
    if applied and changed:
        event = DomainChangedEvent(
            event_id=uuid4(),
            aggregate_id=entity,
            aggregate_version=version,
            event_type="catalog.item.changed",
            payload_schema_version="1.0",
            occurred_at=committed_at,
            projection_task_id=task,
            previous_state_hash=state_hash(version - 1) if version > 1 else None,
            result_state_hash=state_hash(version),
            payload={"version": version, "note": "укр текст", "pad": "x" * payload_size},
        )
        encoded = encode_event(event)
        fields = {
            "event_id": encoded.event_id,
            "event_bytes": encoded.event_bytes,
            "event_media_type": encoded.event_media_type,
            "event_sha256": encoded.event_sha256,
        }
    return AppliedProjectionReceipt(
        projection_task_id=task,
        entity_uuid=entity,
        projection_version=version,
        target_collection="catalog_items_current",
        document_id=entity,
        applied_to_current=applied,
        state_changed=changed,
        previous_version=version - 1 if version > 1 else None,
        previous_hash=state_hash(version - 1) if changed and version > 1 else None,
        result_version=version,
        result_hash=state_hash(version),
        committed_at=committed_at,
        cluster_time=f"1790000000:{version}",
        **fields,
    )


def receipt_document(**kwargs: Any) -> dict[str, Any]:
    return receipt_to_document(receipt(**kwargs))


def at(minutes: int = 0) -> datetime:
    return T0 + timedelta(minutes=minutes)
