"""In-memory `ArtifactStore` для unit-тестів споживачів без мережі."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from collector.storage.store import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactTooLargeError,
    ObjectHead,
    StoredObject,
    _validate_digest,
    _validate_location,
)


@dataclass(frozen=True, slots=True)
class _MemoryObject:
    data: bytes
    sha256: str
    media_type: str


class FakeArtifactStore:
    """Детермінований fake з тим самим integrity contract, що S3 реалізація."""

    def __init__(self, *, buckets: set[str] | None = None) -> None:
        self._buckets = set(buckets or {"raw", "normalized", "events", "archive"})
        self._objects: dict[tuple[str, str], _MemoryObject] = {}
        self.put_calls: list[tuple[str, str]] = []
        self.head_calls: list[tuple[str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    async def put(
        self, bucket: str, key: str, data: bytes, *, sha256: str, media_type: str
    ) -> StoredObject:
        _validate_location(bucket, key)
        _validate_digest(sha256)
        if bucket not in self._buckets:
            raise ArtifactNotFoundError(f"bucket {bucket!r} не існує")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ArtifactIntegrityError("supplied sha256 не збігається з bytes")
        self.put_calls.append((bucket, key))
        self._objects[(bucket, key)] = _MemoryObject(bytes(data), sha256, media_type)
        return StoredObject(
            bucket=bucket,
            key=key,
            size=len(data),
            sha256=sha256,
            media_type=media_type,
        )

    async def head(self, bucket: str, key: str) -> ObjectHead:
        _validate_location(bucket, key)
        self.head_calls.append((bucket, key))
        item = self._objects.get((bucket, key))
        if item is None:
            raise ArtifactNotFoundError(f"s3://{bucket}/{key} не існує")
        return ObjectHead(size=len(item.data), sha256=item.sha256, media_type=item.media_type)

    async def get(self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int) -> bytes:
        return b"".join(
            [
                chunk
                async for chunk in self.get_stream(
                    bucket, key, expected_sha256=expected_sha256, max_bytes=max_bytes
                )
            ]
        )

    async def _stream(
        self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int | None
    ) -> AsyncIterator[bytes]:
        item = self._objects.get((bucket, key))
        if item is None:
            raise ArtifactNotFoundError(f"s3://{bucket}/{key} не існує")
        if (
            item.sha256 != expected_sha256
            or hashlib.sha256(item.data).hexdigest() != expected_sha256
        ):
            raise ArtifactIntegrityError(f"s3://{bucket}/{key}: sha256 mismatch")
        if max_bytes is not None and len(item.data) > max_bytes:
            raise ArtifactTooLargeError(
                f"s3://{bucket}/{key}: {len(item.data)} bytes перевищує limit {max_bytes}"
            )
        yield item.data

    def get_stream(
        self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int | None = None
    ) -> AsyncIterator[bytes]:
        return self._stream(bucket, key, expected_sha256=expected_sha256, max_bytes=max_bytes)

    async def delete(self, bucket: str, key: str) -> None:
        _validate_location(bucket, key)
        self.delete_calls.append((bucket, key))
        self._objects.pop((bucket, key), None)

    async def check_ready(self, bucket: str) -> None:
        if bucket not in self._buckets:
            raise ArtifactNotFoundError(f"bucket {bucket!r} не існує")

    def corrupt(self, bucket: str, key: str, data: bytes) -> None:
        """Adversarial helper: змінити body, залишивши стару metadata sha256."""
        item = self._objects[(bucket, key)]
        self._objects[(bucket, key)] = _MemoryObject(data, item.sha256, item.media_type)


__all__ = ["FakeArtifactStore"]
