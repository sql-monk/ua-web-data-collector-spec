"""Canonical content-addressed object keys shared by producers and consumers."""

from __future__ import annotations

import hashlib
from uuid import UUID

from collector.contracts import NormalizedArtifactRef
from collector.storage.store import (
    ArtifactIntegrityError,
    ArtifactStore,
    StoredObject,
    _validate_digest,
)


def raw_object_key(sha256: str) -> str:
    """Return the only valid raw-object key for a digest (§9.3)."""
    _validate_digest(sha256)
    return f"raw/{sha256}"


def normalized_object_key(entity_uuid: UUID, sha256: str) -> str:
    """Return the canonical normalized JSON key (D-2 condition 1)."""
    _validate_digest(sha256)
    return f"normalized/{entity_uuid}/{sha256}.json"


async def put_normalized(
    store: ArtifactStore,
    bucket: str,
    ref: NormalizedArtifactRef,
    body: bytes,
) -> StoredObject:
    """Store normalized bytes only at the key derived from entity and content digest.

    A target collection schema change must also change ``ref.parser_version``.  The parse
    handler owned by WP-05 enforces that release rule; this helper enforces byte identity and
    the single object-key mapping so callers cannot create an interchangeable second layout.
    """
    actual = hashlib.sha256(body).hexdigest()
    if actual != ref.sha256 or len(body) != ref.size_bytes:
        raise ArtifactIntegrityError("normalized body size/sha256 does not match its reference")
    key = normalized_object_key(ref.entity_uuid, ref.sha256)
    expected_uri = f"s3://{bucket}/{key}"
    if ref.uri != expected_uri:
        raise ValueError(f"normalized artifact uri must be {expected_uri}")
    return await store.put(
        bucket,
        key,
        body,
        sha256=ref.sha256,
        media_type=ref.media_type,
    )


__all__ = ["normalized_object_key", "put_normalized", "raw_object_key"]
