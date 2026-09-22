"""§7.3 кроки 2–4 (R-30, R-36, R-37): command ≠ event, receipt invariants, should_emit."""

from __future__ import annotations

import hashlib

import pytest
from factories import (
    ENTITY_A,
    ENTITY_B,
    EVENT_ID,
    SHA_A,
    TASK_ID,
    at,
    normalized_artifact,
    receipt_payload,
)
from pydantic import ValidationError

from collector.contracts.artifacts import ArtifactRef
from collector.contracts.events import EVENT_INLINE_LIMIT_BYTES, DomainChangedEvent
from collector.contracts.projection import (
    AppliedProjectionReceipt,
    ProjectionAcknowledgement,
    ProjectionCommand,
    should_emit_domain_changed,
)

EVENT_BYTES = b'{"event_id":"33333333-3333-4333-8333-333333333333"}'
EVENT_SHA = hashlib.sha256(EVENT_BYTES).hexdigest()


def applied_changed(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "applied_to_current": True,
        "state_changed": True,
        "previous_version": 2,
        "previous_hash": "v1:" + "c" * 64,
        "result_version": 3,
        "result_hash": "v1:" + "d" * 64,
        "event_id": str(EVENT_ID),
        "event_bytes": EVENT_BYTES,
        "event_media_type": "application/json",
        "event_sha256": EVENT_SHA,
    }
    return receipt_payload(**{**base, **overrides})


def test_projection_command_is_not_a_domain_event() -> None:
    command = ProjectionCommand(
        task_id=TASK_ID,
        entity_uuid=ENTITY_A,
        projection_version=3,
        target_collection="catalog_items_current",
        target_schema_version="1.0",
        artifact=normalized_artifact(),
        issued_at=at(),
    )
    assert not isinstance(command, DomainChangedEvent)
    assert "event_id" not in ProjectionCommand.model_fields
    assert "projection_version" in ProjectionCommand.model_fields  # R-36: обов'язкова версія
    with pytest.raises(ValidationError, match="entity_uuid"):
        command.model_validate(
            {**command.model_dump(), "artifact": normalized_artifact(ENTITY_B).model_dump()}
        )
    with pytest.raises(ValidationError):
        command.model_validate({**command.model_dump(), "projection_version": 0})


@pytest.mark.parametrize(
    ("applied", "changed", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_should_emit_domain_changed_rule(applied: bool, changed: bool, expected: bool) -> None:
    receipt = AppliedProjectionReceipt.model_construct(
        applied_to_current=applied, state_changed=changed
    )
    assert should_emit_domain_changed(receipt) is expected


def test_receipt_event_descriptor_present_iff_applied_and_changed() -> None:
    receipt = AppliedProjectionReceipt.model_validate(applied_changed())
    assert should_emit_domain_changed(receipt)
    assert receipt.event_bytes == EVENT_BYTES
    # applied+changed без descriptor — помилка
    with pytest.raises(ValidationError, match="event_bytes або event_artifact"):
        AppliedProjectionReceipt.model_validate(
            applied_changed(event_bytes=None, event_media_type=None, event_sha256=None)
        )
    # не applied — descriptor заборонений
    with pytest.raises(ValidationError, match="заборонений"):
        AppliedProjectionReceipt.model_validate(
            receipt_payload(
                event_bytes=EVENT_BYTES, event_media_type="application/json", event_sha256=EVENT_SHA
            )
        )
    with pytest.raises(ValidationError, match="заборонений"):
        AppliedProjectionReceipt.model_validate(receipt_payload(event_id=str(EVENT_ID)))
    # heartbeat: applied, але state не змінився — без event
    heartbeat = AppliedProjectionReceipt.model_validate(
        receipt_payload(
            applied_to_current=True, state_changed=False, previous_version=2, result_version=3
        )
    )
    assert not should_emit_domain_changed(heartbeat)


def test_receipt_hash_size_and_version_invariants() -> None:
    with pytest.raises(ValidationError, match="event_sha256"):
        AppliedProjectionReceipt.model_validate(applied_changed(event_sha256="0" * 64))
    with pytest.raises(ValidationError, match="event_media_type"):
        AppliedProjectionReceipt.model_validate(applied_changed(event_media_type=None))
    big = b"x" * (EVENT_INLINE_LIMIT_BYTES + 1)
    with pytest.raises(ValidationError, match="event_artifact"):
        AppliedProjectionReceipt.model_validate(
            applied_changed(event_bytes=big, event_sha256=hashlib.sha256(big).hexdigest())
        )
    with pytest.raises(ValidationError, match="result_version == projection_version"):
        AppliedProjectionReceipt.model_validate(applied_changed(result_version=4))
    with pytest.raises(ValidationError, match="result_version < projection_version"):
        AppliedProjectionReceipt.model_validate(receipt_payload(result_version=2))
    with pytest.raises(ValidationError, match="previous_hash == result_hash"):
        AppliedProjectionReceipt.model_validate(applied_changed(previous_hash="v1:" + "d" * 64))


def test_receipt_with_event_artifact_instead_of_bytes() -> None:
    artifact = ArtifactRef(
        uri="s3://events/" + SHA_A, sha256=SHA_A, size_bytes=300_000, media_type="application/json"
    )
    receipt = AppliedProjectionReceipt.model_validate(
        applied_changed(
            event_bytes=None, event_media_type=None, event_sha256=None, event_artifact=artifact
        )
    )
    ack = ProjectionAcknowledgement.from_receipt(receipt, acknowledged_at=at(2))
    assert ack.event_sha256 == SHA_A
    assert ack.applied_to_current and ack.state_changed
    assert ack.receipt_cluster_time == receipt.cluster_time


def test_receipt_out_of_order_task_does_not_lower_current() -> None:
    # старіша task (v2) прийшла після v3: exact version record є, current не змінено
    receipt = AppliedProjectionReceipt.model_validate(
        receipt_payload(
            projection_version=2, applied_to_current=False, state_changed=False, result_version=3
        )
    )
    assert receipt.result_version > receipt.projection_version
    assert not should_emit_domain_changed(receipt)


def test_receipt_json_round_trip_keeps_bytes() -> None:
    receipt = AppliedProjectionReceipt.model_validate(applied_changed())
    restored = AppliedProjectionReceipt.model_validate_json(receipt.model_dump_json())
    assert restored.event_bytes == EVENT_BYTES
    assert restored == receipt
