"""§9.2/§9.4: current document contract і детермінований state_hash."""

from __future__ import annotations

import unicodedata

import pytest
from factories import ENTITY_A, current_document_payload
from pydantic import ValidationError

from collector.contracts.current import CurrentDocumentBase, compute_state_hash_v1
from collector.contracts.enums import EntityKind


def test_current_document_matches_spec_9_2_shape(current_document: CurrentDocumentBase) -> None:
    dumped = current_document.model_dump(by_alias=True, mode="json")
    assert list(dumped) == [
        "schema_version",
        "_id",
        "entity_kind",
        "source",
        "identity_hash",
        "projection_version",
        "state_hash",
        "core",
        "attributes",
        "latest_state",
        "lineage",
        "time",
        "first_seen_at",
        "last_seen_at",
    ]
    assert set(dumped["source"]) == {"source_id", "source_item_id", "canonical_url"}
    assert set(dumped["lineage"]) == {
        "fetch_id",
        "raw_sha256",
        "parser_version",
        "projection_task_id",
    }
    assert set(dumped["time"]) == {
        "source_event_at",
        "source_updated_at",
        "observed_at",
        "fetched_at",
        "ingested_at",
        "source_timezone_raw",
        "source_time_precision",
        "source_time_inferred",
    }
    assert current_document.entity_uuid == ENTITY_A
    assert current_document.entity_kind is EntityKind.CATALOG_ITEM
    assert dumped["schema_version"] == 1  # §9.2 YAML: int major
    with pytest.raises(ValidationError, match="major"):
        CurrentDocumentBase.model_validate(current_document_payload(schema_version=2))
    with pytest.raises(ValidationError):
        CurrentDocumentBase.model_validate(current_document_payload(schema_version="1.0"))


def test_current_document_accepts_populate_by_name_id() -> None:
    payload = current_document_payload()
    payload["id"] = payload.pop("_id")
    assert CurrentDocumentBase.model_validate(payload).id == ENTITY_A


def test_state_hash_independent_of_field_order_and_unicode_form() -> None:
    core = {"title": "Дриль", "brand": "Bosch"}
    attributes = {"b": 2, "a": {"y": 1, "x": [1, 2]}}
    latest = {"status": "active", "price": {"currency": "UAH", "amount_minor": 100}}
    base = compute_state_hash_v1(core, attributes, latest)
    reordered = compute_state_hash_v1(
        {"brand": "Bosch", "title": "Дриль"},
        {"a": {"x": [1, 2], "y": 1}, "b": 2},
        {"price": {"amount_minor": 100, "currency": "UAH"}, "status": "active"},
    )
    nfd = compute_state_hash_v1(
        {"title": unicodedata.normalize("NFD", "Дриль"), "brand": "Bosch"}, attributes, latest
    )
    assert base == reordered == nfd
    assert base.startswith("v1:")
    assert compute_state_hash_v1(core, attributes, {**latest, "status": "inactive"}) != base
    assert compute_state_hash_v1(core, {}, latest) != base  # attributes входять у hash
    # core/attributes/latest_state — різні блоки, не «злиті» в один словник
    assert compute_state_hash_v1({"k": 1}, {}, {}) != compute_state_hash_v1({}, {"k": 1}, {})


def test_current_document_rejects_stale_state_hash_and_bad_time_order() -> None:
    with pytest.raises(ValidationError, match="state_hash"):
        CurrentDocumentBase.model_validate(current_document_payload(state_hash="v1:" + "0" * 64))
    with pytest.raises(ValidationError, match="last_seen_at"):
        CurrentDocumentBase.model_validate(
            current_document_payload(first_seen_at="2026-09-02T00:00:00Z")
        )
    with pytest.raises(ValidationError):
        CurrentDocumentBase.model_validate(current_document_payload(identity_hash="abc"))
    with pytest.raises(ValidationError):
        CurrentDocumentBase.model_validate(current_document_payload(entity_kind="product"))
    with pytest.raises(ValidationError, match="extra"):
        CurrentDocumentBase.model_validate(
            current_document_payload(status="active")
        )  # R-18: немає status поза осями


def test_current_document_time_block_rejects_fetched_at_as_source_event() -> None:
    payload = current_document_payload()
    payload["time"]["source_event_at"] = payload["time"]["fetched_at"]
    with pytest.raises(ValidationError, match="fetched_at"):
        CurrentDocumentBase.model_validate(payload)


def test_current_document_metadata_only_body_nullable() -> None:
    # R-20: core може мати nullable body_* поля — контракт не вимагає повнотекстовості
    payload = current_document_payload()
    payload["core"] = {
        **payload["core"],
        "body_original_text": None,
        "content_access": "metadata_only",
    }
    payload["state_hash"] = compute_state_hash_v1(
        payload["core"], payload["attributes"], payload["latest_state"]
    )
    doc = CurrentDocumentBase.model_validate(payload)
    assert doc.core["body_original_text"] is None
