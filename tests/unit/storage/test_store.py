from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from collector.contracts import NormalizedArtifactRef
from collector.contracts.enums import DataDomain
from collector.storage import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStore,
    ArtifactTooLargeError,
    S3Credentials,
    StorageConfigError,
    StorageSettings,
    load_credentials,
    normalized_object_key,
    put_normalized,
    raw_object_key,
)
from collector.storage.testing import FakeArtifactStore


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_credentials_file_is_strict_and_repr_hides_secret(tmp_path: Path) -> None:
    path = tmp_path / "minio_fetcher"
    path.write_text("access_key=collector-fetcher\nsecret_key=top-secret\n", encoding="utf-8")
    credentials = load_credentials(path)
    assert credentials == S3Credentials("collector-fetcher", "top-secret")
    assert "top-secret" not in repr(credentials)
    assert "collector-fetcher" in repr(credentials)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "access_key=a\n",
        "secret_key=b\n",
        "access_key=a\nsecret_key=b\nextra=c\n",
        "access_key=a\naccess_key=b\nsecret_key=c\n",
        "access_key=\nsecret_key=b\n",
    ],
)
def test_credentials_file_rejects_incomplete_or_unknown_content(tmp_path: Path, body: str) -> None:
    path = tmp_path / "credentials"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(StorageConfigError, match="credentials file"):
        load_credentials(path)


def test_settings_require_file_and_reject_endpoint_userinfo(tmp_path: Path) -> None:
    with pytest.raises(StorageConfigError, match="COLLECTOR_MINIO_CREDENTIALS_FILE"):
        StorageSettings.from_env({})
    path = tmp_path / "credentials"
    path.write_text("access_key=a\nsecret_key=b\n", encoding="utf-8")
    with pytest.raises(StorageConfigError, match="without userinfo|без userinfo"):
        StorageSettings.from_env(
            {
                "COLLECTOR_MINIO_URL": "http://user:password@minio:9000",
                "COLLECTOR_MINIO_CREDENTIALS_FILE": str(path),
            }
        )


async def test_fake_implements_public_contract_and_checks_integrity() -> None:
    store = FakeArtifactStore(buckets={"raw"})
    assert isinstance(store, ArtifactStore)
    data = b"artifact-body"
    sha256 = digest(data)
    stored = await store.put("raw", f"sha256/{sha256}", data, sha256=sha256, media_type="text/html")
    assert stored.uri == f"s3://raw/sha256/{sha256}"
    assert await store.get("raw", f"sha256/{sha256}", expected_sha256=sha256, max_bytes=100) == data
    assert (
        b"".join(
            [
                chunk
                async for chunk in store.get_stream(
                    "raw", f"sha256/{sha256}", expected_sha256=sha256
                )
            ]
        )
        == data
    )

    with pytest.raises(ArtifactTooLargeError):
        await store.get("raw", f"sha256/{sha256}", expected_sha256=sha256, max_bytes=3)
    with pytest.raises(ArtifactIntegrityError):
        await store.get("raw", f"sha256/{sha256}", expected_sha256="0" * 64, max_bytes=100)
    store.corrupt("raw", f"sha256/{sha256}", b"corrupt")
    with pytest.raises(ArtifactIntegrityError):
        await store.get("raw", f"sha256/{sha256}", expected_sha256=sha256, max_bytes=100)


async def test_fake_delete_and_unknown_bucket_are_explicit() -> None:
    store = FakeArtifactStore(buckets={"raw"})
    data = b"x"
    key = f"sha256/{digest(data)}"
    await store.put("raw", key, data, sha256=digest(data), media_type="application/octet-stream")
    await store.delete("raw", key)
    with pytest.raises(ArtifactNotFoundError):
        await store.head("raw", key)
    with pytest.raises(ArtifactNotFoundError):
        await store.check_ready("missing")


async def test_put_rejects_wrong_digest_before_write() -> None:
    store = FakeArtifactStore(buckets={"raw"})
    with pytest.raises(ArtifactIntegrityError):
        await store.put("raw", "x", b"body", sha256="0" * 64, media_type="text/plain")
    assert store.put_calls == []


@pytest.mark.parametrize(
    ("bucket", "key"),
    [("", "x"), ("raw/path", "x"), ("raw", ""), ("raw", "/absolute")],
)
async def test_location_is_validated(bucket: str, key: str) -> None:
    store = FakeArtifactStore(buckets={"raw"})
    with pytest.raises(ValueError):
        await store.put(bucket, key, b"", sha256=digest(b""), media_type="text/plain")


async def test_canonical_keys_and_normalized_put() -> None:
    store = FakeArtifactStore(buckets={"normalized"})
    body = b'{"name":"item"}'
    sha256 = digest(body)
    entity_uuid = UUID("018f0000-0000-7000-8000-000000000001")
    key = normalized_object_key(entity_uuid, sha256)
    assert key == f"normalized/{entity_uuid}/{sha256}.json"
    assert raw_object_key(sha256) == f"raw/{sha256}"
    ref = NormalizedArtifactRef(
        uri=f"s3://normalized/{key}",
        sha256=sha256,
        size_bytes=len(body),
        media_type="application/json",
        schema_version="1.0",
        entity_uuid=entity_uuid,
        domain=DataDomain.CATALOG,
        parser_version="catalog-1.0",
        fetch_id=UUID("018f0000-0000-7000-8000-000000000002"),
        raw_sha256="0" * 64,
        raw_uri="s3://raw/raw/" + "0" * 64,
        produced_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    stored = await put_normalized(store, "normalized", ref, body)
    assert stored.key == key

    invalid = ref.model_copy(update={"uri": f"s3://normalized/{sha256}.json"})
    with pytest.raises(ValueError, match="normalized artifact uri"):
        await put_normalized(store, "normalized", invalid, body)
