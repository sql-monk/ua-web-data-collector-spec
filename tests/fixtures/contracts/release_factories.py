"""Фабрики release manifest для adversarial-тестів (тестувальник WP-01C); pure, без I/O."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from factories import ENTITY_A, ENTITY_B, SHA_A, SHA_B, at

from collector.contracts.enums import ReleaseState
from collector.contracts.release import ReleaseManifest, ReleasePart, transition_release

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
