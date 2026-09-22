"""§7.3/§10 п.5 (R-27, R-38, R-41): artifact refs і commit predicate upload claim."""

from __future__ import annotations

import pytest
from factories import ENTITY_A, FETCH_ID, SHA_A, SHA_B, at, normalized_artifact
from pydantic import ValidationError

from collector.contracts.artifacts import (
    ArtifactRef,
    RawArtifactRef,
    UploadClaim,
    can_commit,
)
from collector.contracts.enums import UploadClaimStatus


def test_artifact_ref_requires_uri_scheme_hash_and_media_type() -> None:
    ref = ArtifactRef(uri="s3://b/k", sha256=SHA_A, size_bytes=0, media_type="text/html")
    assert ref.schema_version is None
    for bad in (
        {"uri": "b/k"},
        {"sha256": "ABC"},
        {"size_bytes": -1},
        {"media_type": "html"},
        {"schema_version": "1"},
    ):
        with pytest.raises(ValidationError):
            ArtifactRef(**{**ref.model_dump(), **bad})


def test_raw_artifact_ref_carries_http_metadata_raw() -> None:
    raw = RawArtifactRef(
        uri="s3://raw/" + SHA_B,
        sha256=SHA_B,
        size_bytes=10,
        media_type="text/html",
        fetch_id=FETCH_ID,
        fetched_at=at(),
        requested_url="https://example.com/a?utm_source=x",
        final_url="https://example.com/a",
        http_status=200,
        etag='W/"abc"',
        last_modified="Tue, 01 Sep 2026 10:00:00 GMT",
    )
    assert raw.last_modified.startswith("Tue")  # type: ignore[union-attr]
    with pytest.raises(ValidationError):
        RawArtifactRef(**{**raw.model_dump(), "http_status": 99})


def test_normalized_artifact_ref_lineage_required() -> None:
    ref = normalized_artifact()
    assert ref.entity_uuid == ENTITY_A and ref.raw_sha256 == SHA_B
    with pytest.raises(ValidationError):
        ref.model_validate({**ref.model_dump(), "parser_version": ""})


def claim(**overrides: object) -> UploadClaim:
    data: dict[str, object] = {
        "object_key": "raw/aa/" + SHA_A,
        "owner": "fetch-worker-1",
        "status": UploadClaimStatus.LEASED,
        "lease_expires_at": at(10),
        "claim_generation": 3,
    }
    data.update(overrides)
    return UploadClaim.model_validate(data)


def test_can_commit_requires_live_lease_same_generation_and_leased_status() -> None:
    assert can_commit(claim(), now=at(9), generation=3)
    assert not can_commit(claim(), now=at(10), generation=3)  # lease рівно закінчився
    assert not can_commit(claim(), now=at(11), generation=3)
    assert not can_commit(claim(), now=at(9), generation=2)  # stale generation після reacquire
    assert not can_commit(claim(), now=at(9), generation=4)
    for status in (
        UploadClaimStatus.COMMITTED,
        UploadClaimStatus.RELEASED,
        UploadClaimStatus.EXPIRED,
    ):
        assert not can_commit(claim(status=status), now=at(9), generation=3)


def test_claim_generation_positive_int() -> None:
    with pytest.raises(ValidationError):
        claim(claim_generation=0)


def test_can_commit_rejects_naive_or_non_utc_now() -> None:
    """Gate 2 T-06: naive `now` — контрактна ValueError, не TypeError із datetime."""
    from datetime import datetime, timedelta, timezone

    with pytest.raises(ValueError, match="aware UTC"):
        can_commit(claim(), now=datetime(2026, 9, 1, 12, 5), generation=3)
    with pytest.raises(ValueError, match="aware UTC"):
        can_commit(claim(), now=at(5).astimezone(timezone(timedelta(hours=3))), generation=3)
