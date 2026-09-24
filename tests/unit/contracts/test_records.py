"""WP-01C PR2 п.2–3: version record, observations, seller contacts, reviews/questions (§9.2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from factories import (
    ENTITY_B,
    entity_version_payload,
    lineage_payload,
    normalized_artifact,
    observation_payload,
    offer_state_hash,
    review_question_payload,
    seller_contact_payload,
)
from pydantic import ValidationError

from collector.contracts.enums import ObservationReason, ReviewQuestionKind
from collector.contracts.records import (
    MAX_CONTACTS_PER_OBSERVATION,
    EntityProjectionVersion,
    ObservationRecord,
    ReviewQuestionRecord,
    SellerContactObservation,
)

OTHER_TASK = UUID("77777777-7777-4777-8777-777777777777")
OLD_HASH = "v1:" + "d" * 64


def version(**overrides: Any) -> EntityProjectionVersion:
    return EntityProjectionVersion.model_validate(entity_version_payload(**overrides))


# --- EntityProjectionVersion --------------------------------------------------------------------


def test_version_record_valid_and_dumps_mongo_id() -> None:
    record = version()
    dumped = record.model_dump(mode="json", by_alias=True)
    assert dumped["_id"] == str(record.id)
    assert "id" not in dumped
    assert EntityProjectionVersion.model_validate(dumped) == record


def test_first_version_has_no_previous_and_is_changed_and_applied() -> None:
    version(previous_version=None, previous_state_hash=None)
    with pytest.raises(ValidationError, match="state_changed"):
        version(previous_version=None, previous_state_hash=None, state_changed=False)
    with pytest.raises(ValidationError, match="applied_to_current"):
        version(previous_version=None, previous_state_hash=None, applied_to_current=False)


def test_previous_version_and_hash_go_together() -> None:
    with pytest.raises(ValidationError, match="разом"):
        version(previous_state_hash=None)
    with pytest.raises(ValidationError, match="разом"):
        version(previous_version=None)


def test_unchanged_state_requires_equal_hashes() -> None:
    record = version(state_changed=False, previous_state_hash=offer_state_hash())
    assert not record.state_changed
    with pytest.raises(ValidationError, match="state_changed"):
        version(state_changed=False)  # previous hash інший
    with pytest.raises(ValidationError, match="state_changed"):
        version(previous_state_hash=offer_state_hash())  # changed=True, hash той самий


def test_late_arrival_is_recorded_but_not_applied_r36() -> None:
    record = version(previous_version=5, applied_to_current=False)
    assert not record.applied_to_current
    with pytest.raises(ValidationError, match="CAS"):
        version(previous_version=5, applied_to_current=True)
    with pytest.raises(ValidationError, match="CAS"):
        version(previous_version=3, applied_to_current=True)  # той самий version — не новіший


def test_snapshot_or_artifact_required() -> None:
    version(snapshot=None)
    version(artifact=None)
    with pytest.raises(ValidationError, match="snapshot або artifact"):
        version(snapshot=None, artifact=None)


def test_snapshot_state_hash_must_match() -> None:
    with pytest.raises(ValidationError, match="snapshot"):
        version(state_hash="v1:" + "0" * 64)


def test_version_artifact_entity_must_match() -> None:
    artifact = normalized_artifact(ENTITY_B).model_dump(mode="json")
    with pytest.raises(ValidationError, match="artifact.entity_uuid"):
        version(artifact=artifact)


def test_version_lineage_task_must_match() -> None:
    with pytest.raises(ValidationError, match="lineage"):
        version(lineage=lineage_payload(OTHER_TASK))


def test_snapshot_is_bounded() -> None:
    data = entity_version_payload()
    data["snapshot"]["attributes"] = {"all_offers": list(range(1000))}
    with pytest.raises(ValidationError, match="unbounded"):
        EntityProjectionVersion.model_validate(data)


def test_snapshot_time_rejects_fetched_at_as_source_time() -> None:
    data = entity_version_payload()
    data["snapshot"]["time"]["source_event_at"] = data["snapshot"]["time"]["fetched_at"]
    with pytest.raises(ValidationError, match="fetched_at"):
        EntityProjectionVersion.model_validate(data)


# --- ObservationRecord --------------------------------------------------------------------------


def test_observation_changed_and_heartbeat() -> None:
    changed = ObservationRecord.model_validate(observation_payload())
    assert changed.reason is ObservationReason.CHANGED
    heartbeat = ObservationRecord.model_validate(observation_payload(reason="heartbeat"))
    assert heartbeat.state_hash == changed.state_hash
    with pytest.raises(ValidationError):
        ObservationRecord.model_validate(observation_payload(reason="manual"))


def test_observation_only_for_offer_or_listing() -> None:
    ObservationRecord.model_validate(observation_payload(entity_kind="vehicle_listing"))
    for kind in ("catalog_item", "seller"):
        with pytest.raises(ValidationError, match="observation лише"):
            ObservationRecord.model_validate(observation_payload(entity_kind=kind))


def test_observation_rejects_naive_observed_at_and_unbounded_values() -> None:
    with pytest.raises(ValidationError, match="naive"):
        ObservationRecord.model_validate(observation_payload(observed_at="2026-09-01T11:59:00"))
    observed = {"values": {"series": list(range(1000))}}
    with pytest.raises(ValidationError, match="unbounded"):
        ObservationRecord.model_validate(observation_payload(observed=observed))


def test_observation_lineage_and_artifact_checks() -> None:
    with pytest.raises(ValidationError, match="lineage"):
        ObservationRecord.model_validate(observation_payload(lineage=lineage_payload(OTHER_TASK)))
    artifact = normalized_artifact(ENTITY_B).model_dump(mode="json")
    with pytest.raises(ValidationError, match="artifact.entity_uuid"):
        ObservationRecord.model_validate(observation_payload(artifact=artifact))


# --- SellerContactObservation --------------------------------------------------------------------


def test_seller_contacts_keep_raw_and_normalized() -> None:
    record = SellerContactObservation.model_validate(seller_contact_payload())
    assert [c.raw for c in record.contacts] == ["044 000-00-00", "Seller@Example.com"]
    assert record.model_dump(mode="json", by_alias=True)["seller_id"] == str(ENTITY_B)


def test_seller_contacts_bounded_and_non_empty() -> None:
    with pytest.raises(ValidationError):
        SellerContactObservation.model_validate(seller_contact_payload(contacts=[]))
    many = [{"kind": "other", "raw": f"c{i}"} for i in range(MAX_CONTACTS_PER_OBSERVATION + 1)]
    with pytest.raises(ValidationError):
        SellerContactObservation.model_validate(seller_contact_payload(contacts=many))


def test_seller_contact_normalized_phone_must_be_e164() -> None:
    contacts = [{"kind": "phone", "raw": "044 000-00-00", "normalized": "0440000000"}]
    with pytest.raises(ValidationError, match="E.164"):
        SellerContactObservation.model_validate(seller_contact_payload(contacts=contacts))


def test_seller_contact_lineage_task_must_match() -> None:
    with pytest.raises(ValidationError, match="lineage"):
        SellerContactObservation.model_validate(
            seller_contact_payload(lineage=lineage_payload(OTHER_TASK))
        )


# --- ReviewQuestionRecord ----------------------------------------------------------------------


def test_review_and_question_records() -> None:
    review = ReviewQuestionRecord.model_validate(review_question_payload())
    assert review.record_kind is ReviewQuestionKind.REVIEW
    question = ReviewQuestionRecord.model_validate(
        review_question_payload(record_kind="question", published_at=None)
    )
    assert question.published_at is None


def test_review_requires_content_version_and_aware_times() -> None:
    with pytest.raises(ValidationError):
        ReviewQuestionRecord.model_validate(review_question_payload(content_version=""))
    with pytest.raises(ValidationError, match="naive"):
        ReviewQuestionRecord.model_validate(
            review_question_payload(published_at="2026-08-30T09:00:00")
        )
    with pytest.raises(ValidationError, match="lineage"):
        ReviewQuestionRecord.model_validate(
            review_question_payload(lineage=lineage_payload(OTHER_TASK))
        )


@pytest.mark.parametrize(
    ("model", "factory"),
    [
        (EntityProjectionVersion, entity_version_payload),
        (ObservationRecord, observation_payload),
        (SellerContactObservation, seller_contact_payload),
        (ReviewQuestionRecord, review_question_payload),
    ],
)
def test_records_reject_unknown_fields_and_other_major(model: Any, factory: Any) -> None:
    with pytest.raises(ValidationError, match="extra"):
        model.model_validate(factory(domain_field="WP-07 має додати через WP-01C"))
    with pytest.raises(ValidationError, match="несумісна"):
        model.model_validate(factory(schema_version="2.0"))
