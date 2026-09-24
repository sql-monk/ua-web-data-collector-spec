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
from collector.contracts.identity import translation_idempotency_key

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
        "schema_version": 1,
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


# --- PR2: normalized payload, version/observation records, news/translation ------------------

ARTICLE_ID = UUID("55555555-5555-4555-8555-555555555551")
ARTICLE_VERSION_ID = UUID("55555555-5555-4555-8555-555555555552")
RECORD_ID = UUID("66666666-6666-4666-8666-666666666661")
OFFER_CORE: dict[str, Any] = {"title": "Дриль Bosch GSB 13 RE", "seller": "Магазин А"}
OFFER_ATTRIBUTES: dict[str, Any] = {"color": "синій"}
OFFER_LATEST: dict[str, Any] = {
    "price": {"amount_minor": 259900, "currency": "UAH"},
    "status": "active",
}
TRANSLATION_PROVIDER = "google-translate-v3"
TRANSLATION_MODEL = "nmt"
TRANSLATION_GLOSSARY = "f" * 64


def lineage_payload(task_id: UUID = TASK_ID) -> dict[str, Any]:
    return {
        "fetch_id": str(FETCH_ID),
        "raw_sha256": SHA_B,
        "parser_version": "catalog-parser/1.0.0",
        "projection_task_id": str(task_id),
    }


def normalized_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "entity_kind": EntityKind.CATALOG_OFFER.value,
        "entity_uuid": str(ENTITY_A),
        "source": {
            "source_id": "catalog_ua_rozetka",
            "source_item_id": "123456789-offer-1",
            "canonical_url": "https://rozetka.com.ua/ua/123456789/p123456789/",
        },
        "identity_hash": "v1:" + "c" * 64,
        "core": dict(OFFER_CORE),
        "attributes": dict(OFFER_ATTRIBUTES),
        "latest_state": dict(OFFER_LATEST),
        "source_time": {
            "source_event_at": None,
            "source_updated_at": "2026-08-31T10:00:00Z",
            "source_timezone_raw": "Europe/Kyiv",
            "source_time_precision": "minute",
            "source_time_inferred": False,
            "source_time_raw_text": "31.08.2026 13:00",
            "source_locale_raw": "uk-UA",
        },
        "system_time": {
            "observed_at": "2026-09-01T11:59:00Z",
            "fetched_at": "2026-09-01T11:59:30.123456Z",
            "ingested_at": "2026-09-01T12:00:00Z",
        },
        "observation": {
            "price": {"amount_minor": 259900, "currency": "UAH"},
            "availability": "in_stock",
            "values": {"old_price_minor": 279900},
        },
    }
    payload.update(overrides)
    return payload


def version_snapshot_payload() -> dict[str, Any]:
    src = normalized_payload()
    return {
        "source": src["source"],
        "identity_hash": src["identity_hash"],
        "core": src["core"],
        "attributes": src["attributes"],
        "latest_state": src["latest_state"],
        "time": {
            "source_event_at": None,
            "source_updated_at": "2026-08-31T10:00:00Z",
            "observed_at": "2026-09-01T11:59:00Z",
            "fetched_at": "2026-09-01T11:59:30.123456Z",
            "ingested_at": "2026-09-01T12:00:00Z",
            "source_timezone_raw": "Europe/Kyiv",
            "source_time_precision": "minute",
            "source_time_inferred": False,
        },
    }


def offer_state_hash() -> str:
    return compute_state_hash_v1(OFFER_CORE, OFFER_ATTRIBUTES, OFFER_LATEST)


def entity_version_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "_id": str(RECORD_ID),
        "entity_uuid": str(ENTITY_A),
        "entity_kind": EntityKind.CATALOG_OFFER.value,
        "projection_version": 3,
        "projection_task_id": str(TASK_ID),
        "target_collection": "catalog_offers_current",
        "state_hash": offer_state_hash(),
        "state_changed": True,
        "previous_version": 2,
        "previous_state_hash": "v1:" + "d" * 64,
        "snapshot": version_snapshot_payload(),
        "artifact": normalized_artifact().model_dump(mode="json"),
        "lineage": lineage_payload(),
        "applied_to_current": True,
        "recorded_at": "2026-09-01T12:00:01Z",
    }
    payload.update(overrides)
    return payload


def observation_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "_id": str(RECORD_ID),
        "entity_uuid": str(ENTITY_A),
        "entity_kind": EntityKind.CATALOG_OFFER.value,
        "projection_version": 3,
        "projection_task_id": str(TASK_ID),
        "reason": "changed",
        "observed_at": "2026-09-01T11:59:00Z",
        "state_hash": offer_state_hash(),
        "observed": normalized_payload()["observation"],
        "artifact": None,
        "lineage": lineage_payload(),
    }
    payload.update(overrides)
    return payload


def seller_contact_payload(**overrides: Any) -> dict[str, Any]:
    # Синтетичні контакти: номер-заглушка без абонента, домен example.com (RFC 2606).
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "_id": str(RECORD_ID),
        "seller_id": str(ENTITY_B),
        "source": {"source_id": "catalog_ua_rozetka", "source_item_id": "seller-42"},
        "contacts": [
            {"kind": "phone", "raw": "044 000-00-00", "normalized": "+380440000000"},
            {"kind": "email", "raw": "Seller@Example.com", "normalized": "seller@example.com"},
        ],
        "observed_at": "2026-09-01T11:59:00Z",
        "projection_version": 1,
        "projection_task_id": str(TASK_ID),
        "lineage": lineage_payload(),
    }
    payload.update(overrides)
    return payload


def review_question_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "_id": str(RECORD_ID),
        "record_kind": "review",
        "parent_item_id": str(ENTITY_C),
        "source": {"source_id": "catalog_ua_rozetka", "source_item_id": "review-9001"},
        "content_version": "2026-08-30T09:00:00Z",
        "published_at": "2026-08-30T09:00:00Z",
        "updated_at": None,
        "observed_at": "2026-09-01T11:59:00Z",
        "projection_task_id": str(TASK_ID),
        "lineage": lineage_payload(),
    }
    payload.update(overrides)
    return payload


def artifact_ref_payload(sha: str = SHA_A, media_type: str = "text/plain") -> dict[str, Any]:
    return {
        "uri": f"s3://artifacts/news/{sha[:2]}/{sha}",
        "sha256": sha,
        "size_bytes": 512,
        "media_type": media_type,
        "schema_version": None,
    }


def news_version_created_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": str(EVENT_ID),
        "event_type": "news.version_created",
        "article_id": str(ARTICLE_ID),
        "article_version_id": str(ARTICLE_VERSION_ID),
        "version_number": 2,
        "source": {"source_id": "news_de_tagesschau", "source_item_id": "artikel-123"},
        "original_language": "de",
        "source_locale_raw": "de-DE",
        "content_access": "full",
        "content_hash": "e" * 64,
        "title_artifact": artifact_ref_payload("1" * 64),
        "lead_artifact": artifact_ref_payload("2" * 64),
        "cleaned_body_artifact": artifact_ref_payload("3" * 64, "text/html"),
        "backfill": False,
        "occurred_at": "2026-09-01T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def translation_key(article_version_id: UUID = ARTICLE_VERSION_ID) -> str:
    return translation_idempotency_key(
        article_version_id, "uk", TRANSLATION_PROVIDER, TRANSLATION_MODEL, TRANSLATION_GLOSSARY
    )


def news_translation_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "article_id": str(ARTICLE_ID),
        "article_version_id": str(ARTICLE_VERSION_ID),
        "target_language": "uk",
        "source_language": "de",
        "provider": TRANSLATION_PROVIDER,
        "model_version": TRANSLATION_MODEL,
        "glossary_version": TRANSLATION_GLOSSARY,
        "source_content_hash": "e" * 64,
        "status": "translated",
        "title": "Заголовок перекладу",
        "lead": "Лід перекладу",
        "body_text": None,
        "body_artifact": artifact_ref_payload("4" * 64, "text/html"),
        "quality_flags": [],
        "character_count": 1834,
        "cost": {"amount_minor": 37, "currency": "USD"},
        "translation_idempotency_key": translation_key(),
        "created_at": "2026-09-01T12:05:00Z",
    }
    payload.update(overrides)
    return payload
