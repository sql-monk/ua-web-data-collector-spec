"""WP-01C PR2 — contract-тести тестувальника (§16.1 рівень 2): canonical bytes, сумісність,
покриття очікувань споживачів (WP-01B PR2, WP-01A PR3b, WP-04 PR2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from factories import news_translation_payload, news_version_created_payload
from pydantic import ValidationError

from collector.contracts._base import VersionedDocument
from collector.contracts.canonical import sha256_hex
from collector.contracts.events import decode_news_version_created, encode_event
from collector.contracts.news import (
    NEWS_VERSION_CREATED_MEDIA_TYPE,
    NewsTranslation,
    NewsVersionCreatedEvent,
)
from collector.contracts.payload import NormalizedProjectionPayload
from collector.contracts.records import (
    EntityProjectionVersion,
    ObservationRecord,
    ReviewQuestionRecord,
    SellerContactObservation,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = REPO_ROOT / "schemas"
DOCS = REPO_ROOT / "tests" / "fixtures" / "contracts" / "documents"
NEWS_FIXTURE = DOCS / "news_version_created.v1.0.json"

# Snapshot canonical bytes fixture-документа `news_version_created.v1.0.json` (1309 bytes).
# Зміна цього hash = зміна формату bytes події → breaking для outbox/consumers (ADR-0003).
NEWS_FIXTURE_EVENT_SHA256 = "bc3c6c4a8f375e04660f6c62a3c088bb67760260242aaa316d4ac35f1c39859a"

# Canonical bytes, виписані вручну за правилами ADR-0003 (sorted keys, без пробілів, UTF-8,
# datetime `.ffffffZ`, UUID lowercase), — незалежно від реалізації encode_event.
HANDWRITTEN_ARGS: dict[str, Any] = {
    "event_id": "33333333-3333-4333-8333-333333333333",
    "article_id": "55555555-5555-4555-8555-555555555551",
    "article_version_id": "55555555-5555-4555-8555-555555555552",
    "version_number": 1,
    "source": {"source_id": "news_de_tagesschau", "source_item_id": "artikel-ü"},
    "original_language": "de",
    "source_locale_raw": None,
    "content_access": "metadata_only",
    "content_hash": "e" * 64,
    "title_artifact": {
        "uri": "s3://artifacts/news/11/" + "1" * 64,
        "sha256": "1" * 64,
        "size_bytes": 10,
        "media_type": "text/plain",
    },
    "lead_artifact": None,
    "cleaned_body_artifact": None,
    "backfill": True,
    "occurred_at": "2026-09-01T12:00:00+00:00",
}
HANDWRITTEN_BYTES = (
    '{"article_id":"55555555-5555-4555-8555-555555555551",'
    '"article_version_id":"55555555-5555-4555-8555-555555555552",'
    '"backfill":true,'
    '"cleaned_body_artifact":null,'
    '"content_access":"metadata_only",'
    '"content_hash":"' + "e" * 64 + '",'
    '"event_id":"33333333-3333-4333-8333-333333333333",'
    '"event_type":"news.version_created",'
    '"lead_artifact":null,'
    '"occurred_at":"2026-09-01T12:00:00.000000Z",'
    '"original_language":"de",'
    '"schema_version":"1.0",'
    '"source":{"source_id":"news_de_tagesschau","source_item_id":"artikel-ü"},'
    '"source_locale_raw":null,'
    '"title_artifact":{"media_type":"text/plain","schema_version":null,'
    '"sha256":"' + "1" * 64 + '","size_bytes":10,'
    '"uri":"s3://artifacts/news/11/' + "1" * 64 + '"},'
    '"version_number":1}'
).encode("utf-8")


def shuffled(value: Any, seed: int) -> Any:
    """Рекурсивно переставляє ключі dict у детермінованому seed-залежному порядку."""
    if isinstance(value, dict):
        order = sorted(value, key=lambda k: sha256_hex(f"{seed}:{k}".encode()))
        return {k: shuffled(value[k], seed) for k in order}
    if isinstance(value, list):
        return [shuffled(v, seed) for v in value]
    return value


# --- canonical bytes `news.version_created` --------------------------------------------------


def test_news_event_bytes_match_handwritten_canonical_form() -> None:
    encoded = encode_event(NewsVersionCreatedEvent.model_validate(HANDWRITTEN_ARGS))
    assert encoded.event_bytes == HANDWRITTEN_BYTES
    assert encoded.event_sha256 == sha256_hex(HANDWRITTEN_BYTES)
    assert encoded.event_media_type == NEWS_VERSION_CREATED_MEDIA_TYPE


def test_news_fixture_event_bytes_match_pinned_snapshot() -> None:
    event = NewsVersionCreatedEvent.model_validate_json(NEWS_FIXTURE.read_text(encoding="utf-8"))
    encoded = encode_event(event)
    assert len(encoded.event_bytes) == 1309
    assert encoded.event_sha256 == NEWS_FIXTURE_EVENT_SHA256
    assert decode_news_version_created(encoded.event_bytes) == event


@pytest.mark.parametrize("seed", range(20))
def test_news_event_bytes_independent_of_key_order(seed: int) -> None:
    base = encode_event(NewsVersionCreatedEvent.model_validate(news_version_created_payload()))
    data = shuffled(news_version_created_payload(), seed)
    again = encode_event(NewsVersionCreatedEvent.model_validate(data))
    assert again.event_bytes == base.event_bytes
    via_json = encode_event(
        NewsVersionCreatedEvent.model_validate_json(json.dumps(data, ensure_ascii=False, indent=3))
    )
    assert via_json.event_bytes == base.event_bytes


PROBE = """
import sys, json
from pathlib import Path
from collector.contracts.news import NewsVersionCreatedEvent
from collector.contracts.events import encode_event
text = Path(sys.argv[1]).read_text(encoding="utf-8")
sys.stdout.write(encode_event(NewsVersionCreatedEvent.model_validate_json(text)).event_sha256)
"""


def test_news_event_bytes_stable_across_processes_and_hash_seeds() -> None:
    hashes = set()
    for seed in ("0", "1", "4242", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1", "LC_ALL": "C"}
        result = subprocess.run(
            [sys.executable, "-c", PROBE, str(NEWS_FIXTURE)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=120,
        )
        hashes.add(result.stdout.strip())
    assert hashes == {NEWS_FIXTURE_EVENT_SHA256}


def test_news_event_nfd_and_nfc_text_encode_identically() -> None:
    nfc = news_version_created_payload(source_locale_raw="de-Ü")
    nfd = news_version_created_payload(source_locale_raw="de-Ü")
    a = encode_event(NewsVersionCreatedEvent.model_validate(nfc))
    b = encode_event(NewsVersionCreatedEvent.model_validate(nfd))
    assert a.event_bytes == b.event_bytes


# --- minor/major compatibility --------------------------------------------------------------


class NewsTranslationV11(NewsTranslation):
    """Симуляція майбутньої minor-версії: нове optional поле (docs/contracts.md §3.1)."""

    contract_version: ClassVar[str] = "1.1"
    schema_version: str = "1.1"
    reviewer_note: str | None = None


def v10_translation() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (DOCS / "news_translation.v1.0.json").read_text(encoding="utf-8")
    )
    return data


def test_new_minor_reader_accepts_old_minor_document() -> None:
    doc = NewsTranslationV11.model_validate(v10_translation())
    assert doc.schema_version == "1.0"
    assert doc.reviewer_note is None


def test_old_reader_rejects_new_minor_document_explicitly() -> None:
    data = v10_translation()
    data["schema_version"] = "1.1"
    with pytest.raises(ValidationError, match="несумісна"):
        NewsTranslation.model_validate(data)
    data["reviewer_note"] = "ok"
    with pytest.raises(ValidationError):
        NewsTranslation.model_validate(data)  # невідоме поле не губиться мовчки


def test_old_minor_document_with_unknown_field_rejected_not_dropped() -> None:
    data = v10_translation()
    data["reviewer_note"] = "ok"
    with pytest.raises(ValidationError, match="reviewer_note"):
        NewsTranslation.model_validate(data)


PR2_VERSIONED: list[tuple[str, type[VersionedDocument]]] = [
    ("normalized_projection_payload", NormalizedProjectionPayload),
    ("entity_projection_version", EntityProjectionVersion),
    ("observation_record", ObservationRecord),
    ("seller_contact_observation", SellerContactObservation),
    ("review_question_record", ReviewQuestionRecord),
    ("news_translation", NewsTranslation),
    ("news_version_created", NewsVersionCreatedEvent),
]


@pytest.mark.parametrize(("name", "model"), PR2_VERSIONED, ids=[n for n, _ in PR2_VERSIONED])
@pytest.mark.parametrize("version", ["2.0", "0.9", "9.0", "1", "1.0.0", "v1.0", "01.0"])
def test_unknown_or_malformed_major_rejected(
    name: str, model: type[VersionedDocument], version: str
) -> None:
    data = json.loads((DOCS / f"{name}.v1.0.json").read_text(encoding="utf-8"))
    model.model_validate(data)
    data["schema_version"] = version
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize(("name", "model"), PR2_VERSIONED, ids=[n for n, _ in PR2_VERSIONED])
def test_missing_schema_version_defaults_to_current_minor(
    name: str, model: type[VersionedDocument]
) -> None:
    data = json.loads((DOCS / f"{name}.v1.0.json").read_text(encoding="utf-8"))
    del data["schema_version"]
    assert model.model_validate(data).schema_version == model.contract_version


# --- очікування споживачів -------------------------------------------------------------------


def schema(group: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (SCHEMAS / group / f"{name}.v1.json").read_text(encoding="utf-8")
    )
    return data


def test_wp01b_payload_input_fields() -> None:
    # WP-01B PR2 Input: entity_kind, source, identity_hash, core, attributes, latest_state,
    # time, observation-поля.
    props = set(schema("events", "normalized_projection_payload")["properties"])
    assert {
        "schema_version",
        "entity_kind",
        "entity_uuid",
        "source",
        "identity_hash",
        "core",
        "attributes",
        "latest_state",
        "source_time",
        "system_time",
        "observation",
    } <= props


@pytest.mark.parametrize(
    ("name", "index_fields"),
    [
        ("entity_projection_version", {"entity_uuid", "projection_version", "projection_task_id"}),
        ("observation_record", {"projection_task_id", "entity_uuid", "observed_at"}),
        ("seller_contact_observation", {"seller_id", "observed_at", "projection_task_id"}),
        ("review_question_record", {"source", "content_version", "parent_item_id"}),
    ],
)
def test_wp01b_index_fields_are_required_in_mongo_snapshots(
    name: str, index_fields: set[str]
) -> None:
    # WP-01B PR1 п.4: indexes §9.2 на ці поля — поля мають бути required (unique index на
    # відсутньому полі = null-колізії).
    assert index_fields <= set(schema("mongo", name)["required"])


def test_wp01b_review_published_at_index_field_exists() -> None:
    assert "published_at" in schema("mongo", "review_question_record")["properties"]


def test_wp04_input_fields_in_news_event() -> None:
    required = set(schema("events", "news_version_created")["required"])
    props = set(schema("events", "news_version_created")["properties"])
    assert {
        "article_version_id",
        "article_id",
        "source",
        "original_language",
        "content_access",
        "content_hash",
        "title_artifact",
        "occurred_at",
    } <= required
    assert {"source_locale_raw", "lead_artifact", "cleaned_body_artifact", "backfill"} <= props


def test_wp04_wp01a_translation_fields() -> None:
    props = set(schema("common", "news_translation")["properties"])
    assert {
        "article_version_id",
        "target_language",
        "provider",
        "model_version",
        "glossary_version",
        "source_content_hash",
        "status",
        "title",
        "lead",
        "body_text",
        "body_artifact",
        "quality_flags",
        "character_count",
        "cost",
        "translation_idempotency_key",
        "created_at",
    } <= props
    required = set(schema("common", "news_translation")["required"])
    assert "translation_idempotency_key" in required  # WP-01A: ON CONFLICT за цим ключем


def test_translation_idempotency_key_same_for_head_and_body_jobs() -> None:
    # WP-04 О-4 / WP-01A PR3b п.4: head і body — одна version за ключем (ON CONFLICT DO NOTHING).
    head = NewsTranslation.model_validate(news_translation_payload(body_artifact=None))
    full = NewsTranslation.model_validate(news_translation_payload())
    assert head.translation_idempotency_key == full.translation_idempotency_key
