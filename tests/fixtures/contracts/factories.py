"""Фабрики для тестів shared-контрактів: фіксовані часи, UUIDv7, valid payload-и.

Імпортується тестами як `factories` (каталог додано в sys.path у conftest, бо pytest працює в
`--import-mode=importlib` без пакета `tests`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from collector.contracts.artifacts import NormalizedArtifactRef
from collector.contracts.current import compute_state_hash_v1
from collector.contracts.enums import DataDomain, EntityKind

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
# Фіксовані UUIDv7 (version nibble 7, variant 10) для детермінованих fixtures/golden.
ENTITY_A = UUID("019997c0-0000-7000-8000-000000000001")
ENTITY_B = UUID("019997c0-0000-7000-8000-000000000002")
ENTITY_C = UUID("019997c0-0000-7000-8000-000000000003")
ENTITY_D = UUID("019997c0-0000-7000-8000-000000000004")
TASK_ID = UUID("11111111-1111-4111-8111-111111111111")
FETCH_ID = UUID("22222222-2222-4222-8222-222222222222")
EVENT_ID = UUID("33333333-3333-4333-8333-333333333333")
GROUP_1 = UUID("44444444-4444-4444-8444-444444444441")
GROUP_2 = UUID("44444444-4444-4444-8444-444444444442")


def at(minutes: int = 0, **kwargs: int) -> datetime:
    """`T0 + minutes` (+ довільні `timedelta` kwargs)."""
    return T0 + timedelta(minutes=minutes, **kwargs)


def normalized_artifact(entity_uuid: UUID = ENTITY_A) -> NormalizedArtifactRef:
    return NormalizedArtifactRef(
        uri="s3://artifacts/normalized/aa/" + SHA_A,
        sha256=SHA_A,
        size_bytes=1024,
        media_type="application/json",
        schema_version="1.0",
        entity_uuid=entity_uuid,
        domain=DataDomain.CATALOG,
        parser_version="catalog-parser/1.0.0",
        fetch_id=FETCH_ID,
        raw_sha256=SHA_B,
        raw_uri="s3://artifacts/raw/bb/" + SHA_B,
        produced_at=at(1),
    )


def current_document_payload(**overrides: Any) -> dict[str, Any]:
    core = {"title": "Дриль Bosch GSB 13 RE", "brand": "Bosch", "gtin": "3165140351515"}
    attributes = {"color": "синій", "power_w": 600}
    latest_state = {"price": {"amount_minor": 259900, "currency": "UAH"}, "status": "active"}
    payload: dict[str, Any] = {
        "_id": str(ENTITY_A),
        "schema_version": "1.0",
        "entity_kind": EntityKind.CATALOG_ITEM.value,
        "source": {
            "source_id": "catalog_ua_rozetka",
            "source_item_id": "123456789",
            "canonical_url": "https://rozetka.com.ua/ua/123456789/p123456789/",
        },
        "identity_hash": "v1:" + "c" * 64,
        "projection_version": 3,
        "state_hash": compute_state_hash_v1(core, attributes, latest_state),
        "core": core,
        "attributes": attributes,
        "latest_state": latest_state,
        "lineage": {
            "fetch_id": str(FETCH_ID),
            "raw_sha256": SHA_B,
            "parser_version": "catalog-parser/1.0.0",
            "projection_task_id": str(TASK_ID),
        },
        "time": {
            "source_event_at": "2026-08-31T10:00:00Z",
            "source_updated_at": None,
            "observed_at": "2026-09-01T11:59:00Z",
            "fetched_at": "2026-09-01T11:59:30.123456Z",
            "ingested_at": "2026-09-01T12:00:00Z",
            "source_timezone_raw": "Europe/Kyiv",
            "source_time_precision": "minute",
            "source_time_inferred": False,
        },
        "first_seen_at": "2026-08-01T00:00:00Z",
        "last_seen_at": "2026-09-01T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def receipt_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "projection_task_id": str(TASK_ID),
        "entity_uuid": str(ENTITY_A),
        "projection_version": 3,
        "target_collection": "catalog_items_current",
        "document_id": str(ENTITY_A),
        "applied_to_current": False,
        "state_changed": False,
        "previous_version": 3,
        "previous_hash": "v1:" + "d" * 64,
        "result_version": 3,
        "result_hash": "v1:" + "d" * 64,
        "committed_at": "2026-09-01T12:00:01Z",
        "cluster_time": "1756728001:1",
    }
    payload.update(overrides)
    return payload
