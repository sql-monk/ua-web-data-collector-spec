"""Adversarial §9.9 / R-46: усі недозволені переходи, мутація published, обхід через `changes`."""

from __future__ import annotations

import pytest
from factories import at
from pydantic import ValidationError
from release_factories import NEXT_RELEASE_ID, draft, manifest_payload, part, publish

from collector.contracts.enums import ReleaseState
from collector.contracts.release import (
    RELEASE_TRANSITIONS,
    ReleaseManifest,
    ReleaseTransitionError,
    can_transition,
    transition_release,
    validate_manifest_update,
)

ALLOWED = {
    (ReleaseState.DRAFT, ReleaseState.BUILDING),
    (ReleaseState.DRAFT, ReleaseState.FAILED),
    (ReleaseState.BUILDING, ReleaseState.VALIDATING),
    (ReleaseState.BUILDING, ReleaseState.FAILED),
    (ReleaseState.VALIDATING, ReleaseState.PUBLISHED),
    (ReleaseState.VALIDATING, ReleaseState.FAILED),
    (ReleaseState.PUBLISHED, ReleaseState.SUPERSEDED),
}
FORBIDDEN = [(s, t) for s in ReleaseState for t in ReleaseState if (s, t) not in ALLOWED]


def manifest_in(state: ReleaseState) -> ReleaseManifest:
    if state is ReleaseState.DRAFT:
        return draft()
    if state is ReleaseState.BUILDING:
        return transition_release(draft(), ReleaseState.BUILDING)
    if state is ReleaseState.VALIDATING:
        return transition_release(
            transition_release(draft(), ReleaseState.BUILDING), ReleaseState.VALIDATING
        )
    if state is ReleaseState.PUBLISHED:
        return publish(draft())
    if state is ReleaseState.FAILED:
        return transition_release(draft(), ReleaseState.FAILED)
    return transition_release(
        publish(draft()), ReleaseState.SUPERSEDED, superseding_release_id=NEXT_RELEASE_ID
    )


@pytest.mark.parametrize(("source", "target"), FORBIDDEN, ids=lambda s: s.value)
def test_every_forbidden_transition_raises(source: ReleaseState, target: ReleaseState) -> None:
    assert can_transition(source, target) is False
    manifest = manifest_in(source)
    with pytest.raises(ReleaseTransitionError, match="не дозволений"):
        transition_release(
            manifest, target, published_at=at(5) if target is ReleaseState.PUBLISHED else None
        )


def test_transition_table_has_29_forbidden_and_7_allowed_pairs() -> None:
    assert len(FORBIDDEN) == 36 - 7
    assert sum(len(v) for v in RELEASE_TRANSITIONS.values()) == 7
    assert RELEASE_TRANSITIONS[ReleaseState.FAILED] == frozenset()
    assert RELEASE_TRANSITIONS[ReleaseState.SUPERSEDED] == frozenset()


@pytest.mark.parametrize(
    "field_changes",
    [
        {"tag": "renamed"},
        {"owner": "someone-else"},
        {"purpose": "changed"},
        {"parts": []},
        {"git_commit": "deadbeef"},
        {"config_hash": "f" * 64},
        {"published_at": at(99)},
        {"created_at": at(-99)},
        {"entity_versions": None, "partition_index_sha256": "a" * 64},
        {"quality_report": {"gates": "tampered"}},
        {"previous_release_id": NEXT_RELEASE_ID},
        {"image_digests": {}},
        {"build_command": "rm -rf"},
    ],
    ids=lambda c: "+".join(c),
)
def test_published_manifest_rejects_every_field_mutation(field_changes: dict[str, object]) -> None:
    published = publish(draft())
    tampered = published.model_copy(update=field_changes)
    with pytest.raises(ReleaseTransitionError, match="immutable"):
        validate_manifest_update(published, tampered)
    # ...і через supersede теж не «протягується»
    with pytest.raises((ReleaseTransitionError, ValidationError)):
        transition_release(
            published,
            ReleaseState.SUPERSEDED,
            superseding_release_id=NEXT_RELEASE_ID,
            **field_changes,
        )


def test_published_manifest_frozen_and_validate_json_round_trip_identical() -> None:
    published = publish(draft())
    with pytest.raises(ValidationError):
        published.state = ReleaseState.DRAFT  # type: ignore[misc]
    again = ReleaseManifest.model_validate_json(published.model_dump_json(by_alias=True))
    validate_manifest_update(published, again)  # ідентичний manifest — не мутація
    assert again == published


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="WP-01C-T-01: validate_manifest_update охороняє лише published, не superseded",
)
def test_superseded_manifest_stays_immutable_like_published() -> None:
    """§9.9: «Published dataset release є immutable», superseded — теж колишній published.

    Знахідка тестувальника: `validate_manifest_update` охороняє лише `state == published`.
    """
    superseded = manifest_in(ReleaseState.SUPERSEDED)
    tampered = superseded.model_copy(update={"tag": "hacked"})
    try:
        validate_manifest_update(superseded, tampered)
    except ReleaseTransitionError:
        accepted = False
    else:
        accepted = True
    assert not accepted, "superseded manifest прийняв зміну tag"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="WP-01C-T-02: transition_release(**changes) приймає state= в обхід таблиці",
)
def test_transition_release_cannot_override_target_state_via_changes() -> None:
    """Знахідка тестувальника: `**changes` може містити `state` і обійти таблицю переходів."""
    try:
        result = transition_release(draft(), ReleaseState.FAILED, state=ReleaseState.VALIDATING)
    except (ReleaseTransitionError, TypeError, ValueError):
        return
    assert result.state is ReleaseState.FAILED, f"draft → {result.state.value} в обхід таблиці"


def test_direct_published_manifest_needs_all_evidence() -> None:
    with pytest.raises(ValidationError, match="published_at"):
        ReleaseManifest.model_validate(
            manifest_payload(
                state="published",
                parts=[part()],
                quality_report={"a": 1},
                reconciliation_result={"b": 1},
            )
        )
    with pytest.raises(ValidationError, match="parts"):
        ReleaseManifest.model_validate(
            manifest_payload(
                state="published",
                published_at=at(5),
                quality_report={"a": 1},
                reconciliation_result={"b": 1},
            )
        )
