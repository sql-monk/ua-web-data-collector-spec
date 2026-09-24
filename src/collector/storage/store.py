"""Async S3/MinIO artifact store з обов'язковою перевіркою цілісності.

ETag не використовується як checksum: multipart ETag не є MD5. Producer записує SHA-256 у
`x-amz-meta-sha256`; readers звіряють metadata, розмір і фактичний digest отриманих bytes.
Bucket завжди передається явно, тому спільний клієнт придатний для raw, normalized, events та
archive і не має прихованої прив'язки до одного namespace.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from aiobotocore.session import get_session  # type: ignore[import-untyped]  # no py.typed marker

MINIO_URL_ENV = "COLLECTOR_MINIO_URL"
MINIO_CREDENTIALS_FILE_ENV = "COLLECTOR_MINIO_CREDENTIALS_FILE"
MINIO_REGION_ENV = "COLLECTOR_MINIO_REGION"
DEFAULT_MINIO_URL = "http://minio:9000"
DEFAULT_REGION = "us-east-1"
SHA256_METADATA_KEY = "sha256"
DEFAULT_CHUNK_SIZE = 1024 * 1024


class StorageError(RuntimeError):
    """Базова помилка artifact store без credentials у повідомленні."""


class StorageConfigError(ValueError):
    """Endpoint або credentials-file відсутній/невалідний."""


class ArtifactNotFoundError(StorageError):
    """S3 object не існує."""


class ArtifactIntegrityError(StorageError):
    """Metadata, розмір або SHA-256 object-а не збігається з очікуванням."""


class ArtifactTooLargeError(StorageError):
    """Object перевищує bounded read limit."""


@dataclass(frozen=True, slots=True, repr=False)
class S3Credentials:
    access_key: str
    secret_key: str

    def __repr__(self) -> str:
        return f"S3Credentials(access_key={self.access_key!r}, secret_key=***)"


@dataclass(frozen=True, slots=True)
class StorageSettings:
    endpoint_url: str
    credentials: S3Credentials
    region: str = DEFAULT_REGION

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> StorageSettings:
        env: Mapping[str, str] = os.environ if environ is None else environ
        endpoint = (env.get(MINIO_URL_ENV) or DEFAULT_MINIO_URL).rstrip("/")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise StorageConfigError(f"{MINIO_URL_ENV}: очікується http(s) endpoint без userinfo")
        raw_path = env.get(MINIO_CREDENTIALS_FILE_ENV, "").strip()
        if not raw_path:
            raise StorageConfigError(f"задайте {MINIO_CREDENTIALS_FILE_ENV}")
        credentials = load_credentials(Path(raw_path))
        return cls(
            endpoint_url=endpoint,
            credentials=credentials,
            region=env.get(MINIO_REGION_ENV, DEFAULT_REGION),
        )


def load_credentials(path: Path) -> S3Credentials:
    """Прочитати WP-00 PR5 secret `access_key=...\nsecret_key=...` fail-closed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise StorageConfigError(f"не вдалося прочитати credentials file {path}") from exc
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in {"access_key", "secret_key"} or key in values or not value:
            raise StorageConfigError(f"credentials file {path}: невалідний формат")
        values[key] = value
    if set(values) != {"access_key", "secret_key"}:
        raise StorageConfigError(f"credentials file {path}: потрібні access_key і secret_key")
    return S3Credentials(values["access_key"], values["secret_key"])


@dataclass(frozen=True, slots=True)
class ObjectHead:
    size: int
    sha256: str
    etag: str | None = None
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class StoredObject(ObjectHead):
    bucket: str = ""
    key: str = ""

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


@runtime_checkable
class ArtifactStore(Protocol):
    """Публічний API для WP-01B/WP-04/WP-05; усі bucket-и явні."""

    async def put(
        self, bucket: str, key: str, data: bytes, *, sha256: str, media_type: str
    ) -> StoredObject: ...

    async def head(self, bucket: str, key: str) -> ObjectHead: ...

    async def get(
        self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int
    ) -> bytes: ...

    def get_stream(
        self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int | None = None
    ) -> AsyncIterator[bytes]: ...

    async def delete(self, bucket: str, key: str) -> None: ...

    async def check_ready(self, bucket: str) -> None: ...


def _validate_location(bucket: str, key: str) -> None:
    if not bucket or "/" in bucket or bucket in {".", ".."}:
        raise ValueError("bucket має бути непорожнім S3 bucket name без '/'")
    if not key or key.startswith("/") or "\x00" in key:
        raise ValueError("key має бути непорожнім відносним object key")


def _validate_digest(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("sha256 має бути 64 lowercase hex")


class S3ArtifactStore:
    """`aiobotocore` client factory; client відкривається на bounded operation/stream."""

    def __init__(self, settings: StorageSettings) -> None:
        self._settings = settings
        self._session = get_session()

    def _client(self) -> Any:
        credentials = self._settings.credentials
        return self._session.create_client(
            "s3",
            endpoint_url=self._settings.endpoint_url,
            region_name=self._settings.region,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
        )

    async def put(
        self, bucket: str, key: str, data: bytes, *, sha256: str, media_type: str
    ) -> StoredObject:
        _validate_location(bucket, key)
        _validate_digest(sha256)
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise ArtifactIntegrityError(
                f"put s3://{bucket}/{key}: supplied sha256 не збігається з bytes"
            )
        async with self._client() as client:
            await client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentLength=len(data),
                ContentType=media_type,
                Metadata={SHA256_METADATA_KEY: sha256},
            )
        verified = await self.head(bucket, key)
        if verified.size != len(data) or verified.sha256 != sha256:
            raise ArtifactIntegrityError(f"put verification failed for s3://{bucket}/{key}")
        return StoredObject(
            bucket=bucket,
            key=key,
            size=verified.size,
            sha256=verified.sha256,
            etag=verified.etag,
            media_type=verified.media_type,
        )

    async def head(self, bucket: str, key: str) -> ObjectHead:
        _validate_location(bucket, key)
        async with self._client() as client:
            try:
                response = await client.head_object(Bucket=bucket, Key=key)
            except client.exceptions.ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in {"404", "NoSuchKey", "NotFound"}:
                    raise ArtifactNotFoundError(f"s3://{bucket}/{key} не існує") from None
                raise StorageError(
                    f"head s3://{bucket}/{key} failed ({code or 'unknown'})"
                ) from None
        metadata = {str(k).lower(): str(v) for k, v in response.get("Metadata", {}).items()}
        digest = metadata.get(SHA256_METADATA_KEY)
        if digest is None:
            raise ArtifactIntegrityError(f"s3://{bucket}/{key}: немає x-amz-meta-sha256")
        _validate_digest(digest)
        return ObjectHead(
            size=int(response["ContentLength"]),
            sha256=digest,
            etag=str(response.get("ETag", "")).strip('"') or None,
            media_type=response.get("ContentType"),
        )

    async def get(self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise ValueError("max_bytes має бути >= 0")
        chunks = [
            chunk
            async for chunk in self.get_stream(
                bucket, key, expected_sha256=expected_sha256, max_bytes=max_bytes
            )
        ]
        return b"".join(chunks)

    async def _stream(
        self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int | None
    ) -> AsyncIterator[bytes]:
        _validate_location(bucket, key)
        _validate_digest(expected_sha256)
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes має бути >= 0 або None")
        head = await self.head(bucket, key)
        if head.sha256 != expected_sha256:
            raise ArtifactIntegrityError(f"s3://{bucket}/{key}: metadata sha256 mismatch")
        if max_bytes is not None and head.size > max_bytes:
            raise ArtifactTooLargeError(
                f"s3://{bucket}/{key}: {head.size} bytes перевищує limit {max_bytes}"
            )
        digest = hashlib.sha256()
        seen = 0
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=bucket, Key=key)
            except client.exceptions.ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in {"404", "NoSuchKey", "NotFound"}:
                    raise ArtifactNotFoundError(f"s3://{bucket}/{key} не існує") from None
                raise StorageError(
                    f"get s3://{bucket}/{key} failed ({code or 'unknown'})"
                ) from None
            body = response["Body"]
            async with body:
                while chunk := await body.read(DEFAULT_CHUNK_SIZE):
                    seen += len(chunk)
                    if max_bytes is not None and seen > max_bytes:
                        raise ArtifactTooLargeError(
                            f"s3://{bucket}/{key}: stream перевищив limit {max_bytes}"
                        )
                    digest.update(chunk)
                    yield bytes(chunk)
        if seen != head.size or digest.hexdigest() != expected_sha256:
            raise ArtifactIntegrityError(f"s3://{bucket}/{key}: body size/sha256 mismatch")

    def get_stream(
        self, bucket: str, key: str, *, expected_sha256: str, max_bytes: int | None = None
    ) -> AsyncIterator[bytes]:
        return self._stream(bucket, key, expected_sha256=expected_sha256, max_bytes=max_bytes)

    async def delete(self, bucket: str, key: str) -> None:
        _validate_location(bucket, key)
        async with self._client() as client:
            await client.delete_object(Bucket=bucket, Key=key)

    async def check_ready(self, bucket: str) -> None:
        if not bucket:
            raise ValueError("bucket має бути непорожнім")
        async with self._client() as client:
            await client.head_bucket(Bucket=bucket)


__all__ = [
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "ArtifactStore",
    "ArtifactTooLargeError",
    "ObjectHead",
    "S3ArtifactStore",
    "S3Credentials",
    "StorageConfigError",
    "StorageError",
    "StorageSettings",
    "StoredObject",
    "load_credentials",
]
