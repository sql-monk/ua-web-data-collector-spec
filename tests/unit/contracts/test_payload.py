"""WP-01C PR2 п.1: `NormalizedProjectionPayload` + перевірка проти `NormalizedArtifactRef`."""

from __future__ import annotations

from typing import Any

import pytest
from factories import (
    ENTITY_A,
    ENTITY_B,
    normalized_artifact,
    normalized_payload,
    offer_state_hash,
)
from pydantic import ValidationError

from collector.contracts._base import MAX_JSON_ARRAY_ITEMS, MAX_JSON_DEPTH, MAX_JSON_OBJECT_KEYS
from collector.contracts.canonical import canonical_json_bytes
from collector.contracts.enums import DataDomain, EntityKind
from collector.contracts.payload import (
    ENTITY_KIND_DOMAINS,
    NormalizedProjectionPayload,
    PayloadArtifactMismatchError,
    check_payload_matches_artifact,
)


def build(**overrides: Any) -> NormalizedProjectionPayload:
    return NormalizedProjectionPayload.model_validate(normalized_payload(**overrides))


def test_valid_payload_exposes_state_hash_and_entity_time() -> None:
    payload = build()
    assert payload.state_hash() == offer_state_hash()
    time = payload.entity_time()
    assert time.source_event_at is None  # невідомий source time не заповнюється
    assert time.observed_at == payload.system_time.observed_at
    assert payload.observation is not None
    assert payload.observation.price is not None
    assert payload.observation.price.amount_minor == 259900


def test_payload_without_observation_and_default_source_time() -> None:
    data = normalized_payload(entity_kind="catalog_item", observation=None)
    del data["source_time"]
    payload = NormalizedProjectionPayload.model_validate(data)
    assert payload.observation is None
    assert payload.source_time.source_event_at is None


def test_matching_artifact_passes() -> None:
    check_payload_matches_artifact(build(), normalized_artifact(ENTITY_A))


def test_entity_uuid_mismatch_with_ref_is_rejected() -> None:
    with pytest.raises(PayloadArtifactMismatchError, match="entity_uuid"):
        check_payload_matches_artifact(build(), normalized_artifact(ENTITY_B))


def test_schema_version_mismatch_with_ref_is_rejected() -> None:
    ref = normalized_artifact().model_copy(update={"schema_version": "1.1"})
    with pytest.raises(PayloadArtifactMismatchError, match="schema_version"):
        check_payload_matches_artifact(build(), ref)


def test_entity_kind_incompatible_with_domain_is_rejected() -> None:
    ref = normalized_artifact().model_copy(update={"domain": DataDomain.VEHICLE})
    with pytest.raises(PayloadArtifactMismatchError, match="domain"):
        check_payload_matches_artifact(build(), ref)
    # seller живе і в catalog, і в vehicle
    check_payload_matches_artifact(build(entity_kind="seller", observation=None), ref)


def test_mismatch_error_is_value_error_for_permanent_mapping() -> None:
    assert issubclass(PayloadArtifactMismatchError, ValueError)


def test_every_entity_kind_has_allowed_domains() -> None:
    assert set(ENTITY_KIND_DOMAINS) == set(EntityKind)
    assert all(ENTITY_KIND_DOMAINS.values())


def test_source_requires_canonical_url_and_known_source_id() -> None:
    data = normalized_payload()
    del data["source"]["canonical_url"]
    with pytest.raises(ValidationError, match="canonical_url"):
        NormalizedProjectionPayload.model_validate(data)
    bad = normalized_payload()
    bad["source"]["source_id"] = "catalog_ua_nonexistent"
    with pytest.raises(ValidationError):
        NormalizedProjectionPayload.model_validate(bad)


def test_fetched_at_as_source_time_is_rejected_r43() -> None:
    data = normalized_payload()
    data["source_time"]["source_updated_at"] = data["system_time"]["fetched_at"]
    with pytest.raises(ValidationError, match="fetched_at"):
        NormalizedProjectionPayload.model_validate(data)


def test_naive_system_time_is_rejected() -> None:
    data = normalized_payload()
    data["system_time"]["observed_at"] = "2026-09-01T11:59:00"
    with pytest.raises(ValidationError, match="naive"):
        NormalizedProjectionPayload.model_validate(data)


def test_non_json_values_in_core_are_rejected() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ValidationError):
        build(core={"at": datetime(2026, 1, 1, tzinfo=UTC)})


def nested(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"leaf": 1}
    for _ in range(depth - 2):
        node = {"n": node}
    return node


@pytest.mark.parametrize("block", ["core", "attributes", "latest_state"])
def test_blocks_are_bounded(block: str) -> None:
    build(**{block: {"items": list(range(MAX_JSON_ARRAY_ITEMS))}})
    with pytest.raises(ValidationError, match="unbounded"):
        build(**{block: {"items": list(range(MAX_JSON_ARRAY_ITEMS + 1))}})
    build(**{block: {f"k{i}": i for i in range(MAX_JSON_OBJECT_KEYS)}})
    with pytest.raises(ValidationError, match="ключів"):
        build(**{block: {f"k{i}": i for i in range(MAX_JSON_OBJECT_KEYS + 1)}})
    build(**{block: nested(MAX_JSON_DEPTH)})
    with pytest.raises(ValidationError, match="вкладеність"):
        build(**{block: nested(MAX_JSON_DEPTH + 1)})


def test_nested_array_inside_array_is_bounded() -> None:
    with pytest.raises(ValidationError, match="unbounded"):
        build(core={"media": [[0] * (MAX_JSON_ARRAY_ITEMS + 1)]})


def test_observation_values_are_bounded() -> None:
    observation = {"values": {"history": list(range(MAX_JSON_ARRAY_ITEMS + 1))}}
    with pytest.raises(ValidationError, match="unbounded"):
        build(observation=observation)


def test_observation_price_is_money_without_float() -> None:
    with pytest.raises(ValidationError):
        build(observation={"price": {"amount_minor": 2599.0, "currency": "UAH"}})


def test_canonical_bytes_round_trip_is_byte_equivalent() -> None:
    payload = build()
    data = canonical_json_bytes(payload)
    again = NormalizedProjectionPayload.model_validate_json(data)
    assert again == payload
    assert canonical_json_bytes(again) == data
    reordered = dict(reversed(list(normalized_payload().items())))
    assert canonical_json_bytes(NormalizedProjectionPayload.model_validate(reordered)) == data
