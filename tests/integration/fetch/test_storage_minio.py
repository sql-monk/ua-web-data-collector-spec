"""ArtifactStore contract against a real MinIO server (no mocks)."""

from __future__ import annotations

import hashlib

import pytest

from collector.storage import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactTooLargeError,
    S3ArtifactStore,
)

pytestmark = pytest.mark.integration


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def test_real_minio_put_head_get_stream_delete_and_readiness(
    minio_store: tuple[S3ArtifactStore, str],
) -> None:
    store, bucket = minio_store
    data = (b"minio-contract-body-" * 100_000) + b"tail"
    sha256 = digest(data)
    key = f"raw/{sha256}"

    await store.check_ready(bucket)
    stored = await store.put(bucket, key, data, sha256=sha256, media_type="text/html")
    assert stored.size == len(data)
    assert stored.sha256 == sha256
    assert stored.uri == f"s3://{bucket}/{key}"
    assert await store.get(bucket, key, expected_sha256=sha256, max_bytes=len(data)) == data
    assert (
        b"".join([chunk async for chunk in store.get_stream(bucket, key, expected_sha256=sha256)])
        == data
    )

    with pytest.raises(ArtifactTooLargeError):
        await store.get(bucket, key, expected_sha256=sha256, max_bytes=len(data) - 1)
    with pytest.raises(ArtifactIntegrityError, match="metadata sha256 mismatch"):
        await store.get(bucket, key, expected_sha256="0" * 64, max_bytes=len(data))

    await store.delete(bucket, key)
    with pytest.raises(ArtifactNotFoundError):
        await store.head(bucket, key)


async def test_real_minio_rejects_missing_checksum_metadata(
    minio_store: tuple[S3ArtifactStore, str],
) -> None:
    store, bucket = minio_store
    async with store._client() as client:  # noqa: SLF001 - adversarial fixture
        await client.put_object(Bucket=bucket, Key="raw/untrusted", Body=b"untrusted")
    with pytest.raises(ArtifactIntegrityError, match="x-amz-meta-sha256"):
        await store.head(bucket, "raw/untrusted")
