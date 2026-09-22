"""§9.9 / R-46: manifest з усіма полями, state machine, immutable published."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from factories import ENTITY_A, ENTITY_B, SHA_A, SHA_B, at
from pydantic import ValidationError

from collector.contracts.enums import ReleaseState
from collector.contracts.release import (
    RELEASE_TRANSITIONS,
    ReleaseManifest,
    ReleasePart,
    ReleaseTransitionError,
    can_transition,
    transition_release,
    validate_manifest_update,
)

RELEASE_ID = UUID("66666666-6666-4666-8666-666666666666")
NEXT_RELEASE_ID = UUID("66666666-6666-4666-8666-666666666667")


def part() -> ReleasePart:
    return ReleasePart(
        uri="s3://releases/r1/catalog/part-000.parquet",
        format="parquet",
        partition={"domain": "catalog", "month": "2026-09"},
        row_count=1000,
        min_effective_at=at(-1000),
        max_effective_at=at(0),
        min_system_at=at(-900),
        max_system_at=at(1),
        size_bytes=123456,
        sha256=SHA_A,
    )


def manifest_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "release_id": RELEASE_ID,
        "tag": "2026-09-research-1",
        "state": ReleaseState.DRAFT,
        "created_at": at(),
        "owner": "research-team",
        "purpose": "monthly research snapshot",
        "watermark": {
            "snapshot_at": at(),
            "postgres_snapshot": "0/16B3748",
            "mongo_cluster_time": "1756728000:1",
            "resolution_snapshot_id": UUID(int=9),
        },
        "entity_versions": [
            {"entity_uuid": ENTITY_A, "projection_version": 3},
            {"entity_uuid": ENTITY_B, "projection_version": 1},
        ],
        "source_registry_version": "1",
        "included_sources": [{"source_id": "catalog_ua_rozetka", "policy_version": "3"}],
        "excluded_sources": [
            {"source_id": "news_us_nyt", "policy_version": "2", "reason": "premium paywall"}
        ],
        "degraded_sources": [
            {"source_id": "news_pl_pap", "policy_version": "1", "reason": "sitemap unavailable"}
        ],
        "versions": {
            "schema_version": "1.0",
            "parser_version": "parsers/1.4.0",
            "normalizer_version": "norm/1.1.0",
            "matcher_version": "matcher/1.0.0",
            "resolution_version": "resolution/1.0.0",
            "translation_provider": "deepl",
            "translation_model_version": "2026-08",
            "glossary_version": "glossary-3",
        },
        "git_commit": "7223bec0",
        "image_digests": {"collector": "sha256:" + SHA_B},
        "config_hash": SHA_B,
        "build_command": "uv run collector release build --watermark test --output out",
        "parts": [],
        "previous_release_id": None,
    }
    payload.update(overrides)
    return payload


def draft() -> ReleaseManifest:
    return ReleaseManifest.model_validate(manifest_payload())


def publish(manifest: ReleaseManifest) -> ReleaseManifest:
    building = transition_release(manifest, ReleaseState.BUILDING)
    validating = transition_release(
        building,
        ReleaseState.VALIDATING,
        parts=[part()],
        quality_report={"gates": "passed"},
        reconciliation_result={"unacknowledged_tasks": 0},
    )
    return transition_release(validating, ReleaseState.PUBLISHED, published_at=at(5))


def test_manifest_has_all_spec_9_9_fields() -> None:
    fields = set(ReleaseManifest.model_fields)
    assert {
        "release_id",
        "tag",
        "created_at",
        "published_at",
        "owner",
        "purpose",
        "watermark",
        "entity_versions",
        "partition_index_sha256",
        "source_registry_version",
        "included_sources",
        "excluded_sources",
        "degraded_sources",
        "versions",
        "git_commit",
        "image_digests",
        "config_hash",
        "build_command",
        "parts",
        "quality_report",
        "reconciliation_result",
        "previous_release_id",
        "superseding_release_id",
        "state",
        "schema_version",
    } <= fields
    assert set(ReleasePart.model_fields) == {
        "uri",
        "format",
        "partition",
        "row_count",
        "min_effective_at",
        "max_effective_at",
        "min_system_at",
        "max_system_at",
        "size_bytes",
        "sha256",
    }


def test_manifest_validation_rules() -> None:
    with pytest.raises(ValidationError, match="entity_versions / partition_index_sha256"):
        ReleaseManifest.model_validate(manifest_payload(entity_versions=None))
    with pytest.raises(ValidationError, match="entity_versions / partition_index_sha256"):
        ReleaseManifest.model_validate(manifest_payload(partition_index_sha256=SHA_A))
    with pytest.raises(ValidationError, match="reason"):
        ReleaseManifest.model_validate(
            manifest_payload(excluded_sources=[{"source_id": "news_us_nyt", "policy_version": "2"}])
        )
    with pytest.raises(ValidationError, match="published_at"):
        ReleaseManifest.model_validate(manifest_payload(published_at=at()))
    with pytest.raises(ValidationError, match="потребує"):
        ReleaseManifest.model_validate(
            manifest_payload(state=ReleaseState.PUBLISHED, published_at=at())
        )
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(manifest_payload(git_commit="not-a-sha"))
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(
            manifest_payload(included_sources=[{"source_id": "x", "policy_version": "1"}])
        )


def test_state_machine_transitions_table() -> None:
    allowed = {
        (ReleaseState.DRAFT, ReleaseState.BUILDING),
        (ReleaseState.DRAFT, ReleaseState.FAILED),
        (ReleaseState.BUILDING, ReleaseState.VALIDATING),
        (ReleaseState.BUILDING, ReleaseState.FAILED),
        (ReleaseState.VALIDATING, ReleaseState.PUBLISHED),
        (ReleaseState.VALIDATING, ReleaseState.FAILED),
        (ReleaseState.PUBLISHED, ReleaseState.SUPERSEDED),
    }
    for source in ReleaseState:
        for target in ReleaseState:
            assert can_transition(source, target) is ((source, target) in allowed), (source, target)
    assert set(RELEASE_TRANSITIONS) == set(ReleaseState)


def test_happy_path_to_published_and_superseded() -> None:
    published = publish(draft())
    assert published.state is ReleaseState.PUBLISHED
    assert published.published_at == at(5)
    superseded = transition_release(
        published, ReleaseState.SUPERSEDED, superseding_release_id=NEXT_RELEASE_ID
    )
    assert superseded.superseding_release_id == NEXT_RELEASE_ID
    with pytest.raises(ReleaseTransitionError):
        transition_release(superseded, ReleaseState.PUBLISHED)


def test_published_is_immutable() -> None:
    published = publish(draft())
    for target in (
        ReleaseState.DRAFT,
        ReleaseState.BUILDING,
        ReleaseState.VALIDATING,
        ReleaseState.FAILED,
        ReleaseState.PUBLISHED,
    ):
        with pytest.raises(ReleaseTransitionError, match="не дозволений"):
            transition_release(published, target)
    # supersede не може «протягнути» зміну parts/tag/watermark
    with pytest.raises(ReleaseTransitionError, match="immutable"):
        transition_release(
            published,
            ReleaseState.SUPERSEDED,
            superseding_release_id=NEXT_RELEASE_ID,
            tag="renamed",
        )
    tampered = ReleaseManifest.model_validate(
        {**published.model_dump(), "parts": [part().model_copy(update={"row_count": 999})]}
    )
    with pytest.raises(ReleaseTransitionError, match="parts"):
        validate_manifest_update(published, tampered)
    with pytest.raises(ReleaseTransitionError, match="release_id"):
        validate_manifest_update(
            published, published.model_copy(update={"release_id": NEXT_RELEASE_ID})
        )
    # frozen: пряме присвоєння теж заборонене
    with pytest.raises(ValidationError):
        published.tag = "x"  # type: ignore[misc]


def test_publish_requires_evidence_and_failed_is_terminal() -> None:
    building = transition_release(draft(), ReleaseState.BUILDING)
    with pytest.raises(ValidationError, match="потребує"):
        transition_release(
            transition_release(building, ReleaseState.VALIDATING),
            ReleaseState.PUBLISHED,
            published_at=at(5),
        )
    failed = transition_release(building, ReleaseState.FAILED)
    assert not any(can_transition(ReleaseState.FAILED, target) for target in ReleaseState)
    with pytest.raises(ReleaseTransitionError):
        transition_release(failed, ReleaseState.BUILDING)


def test_part_time_ranges_validated() -> None:
    with pytest.raises(ValidationError, match="max time"):
        part().model_validate({**part().model_dump(), "max_effective_at": at(-2000)})


def test_superseded_is_immutable_except_missing_link() -> None:
    """Gate 2 T-01: superseded (колишній published) не приймає зміну жодного поля."""
    superseded = transition_release(
        publish(draft()), ReleaseState.SUPERSEDED, superseding_release_id=NEXT_RELEASE_ID
    )
    for field, value in (
        ("tag", "renamed"),
        ("parts", []),
        ("config_hash", SHA_A),
        ("superseding_release_id", UUID(int=77)),
        ("state", ReleaseState.PUBLISHED),
    ):
        tampered = superseded.model_copy(update={field: value})
        with pytest.raises(ReleaseTransitionError, match="superseded release immutable"):
            validate_manifest_update(superseded, tampered)
    validate_manifest_update(superseded, superseded)  # ідентичний — не мутація


def test_transition_release_rejects_state_and_release_id_in_changes() -> None:
    """Gate 2 T-02: `state`/`release_id` у `**changes` не обходять RELEASE_TRANSITIONS."""
    with pytest.raises(ReleaseTransitionError, match="state"):
        transition_release(draft(), ReleaseState.FAILED, state=ReleaseState.VALIDATING)
    with pytest.raises(ReleaseTransitionError, match="release_id"):
        transition_release(draft(), ReleaseState.BUILDING, release_id=NEXT_RELEASE_ID)
    assert transition_release(draft(), ReleaseState.FAILED).state is ReleaseState.FAILED
