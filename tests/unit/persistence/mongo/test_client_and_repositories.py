"""Фабрика клієнта (concerns §8, R-34) і BSON mapping receipt-а — без з'єднання з сервером."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from bson import BSON, Binary
from bson.binary import UuidRepresentation
from bson.codec_options import CodecOptions
from mongo_factories import receipt
from pymongo import ReadPreference

from collector.persistence.mongo import repositories
from collector.persistence.mongo.client import (
    MongoConfigError,
    MongoSettings,
    create_client,
    transaction_options,
)
from collector.persistence.mongo.repositories import (
    receipt_from_document,
    receipt_to_document,
)

STANDARD = CodecOptions(uuid_representation=UuidRepresentation.STANDARD, tz_aware=True)


def _uri(password: str, path: str = "") -> str:
    return f"mongodb://collector_projector:{password}@mongo:27017/{path}?replicaSet=rs0"


def test_settings_from_env_file_and_database(tmp_path: Path) -> None:
    password = secrets.token_hex(8)
    secret = tmp_path / "mongo_uri_projector"
    secret.write_text(_uri(password) + "\n", encoding="utf-8")
    settings = MongoSettings.from_env({"COLLECTOR_MONGO_URI_FILE": str(secret)})
    assert settings.database == "collector"
    assert password not in repr(settings)
    assert settings.redacted_uri == "mongodb://mongo:27017"
    with_db = MongoSettings.from_env({"COLLECTOR_MONGO_URI": _uri(password, "domain")})
    assert with_db.database == "domain"
    env_db = MongoSettings.from_env(
        {"COLLECTOR_MONGO_URI": _uri(password), "COLLECTOR_MONGO_DATABASE": "other"}
    )
    assert env_db.database == "other"


def test_settings_errors_do_not_leak_uri() -> None:
    with pytest.raises(MongoConfigError, match="COLLECTOR_MONGO_URI"):
        MongoSettings.from_env({})
    password = secrets.token_hex(8)
    with pytest.raises(MongoConfigError) as info:
        MongoSettings.from_env({"COLLECTOR_MONGO_URI": f"mongodb://u:{password}@/bad?x=y"})
    assert password not in str(info.value)
    assert info.value.__cause__ is None


async def test_client_concerns_match_section_8() -> None:
    settings = MongoSettings(uri=_uri(secrets.token_hex(8)))
    client = create_client(settings)
    try:
        assert client.read_preference == ReadPreference.PRIMARY
        assert client.read_concern.level == "majority"
        assert client.write_concern.document == {"w": "majority"}
        assert client.options.retry_writes is True
        assert client.codec_options.uuid_representation == UuidRepresentation.STANDARD
        assert client.codec_options.tz_aware is True
        assert (
            client.options.server_selection_timeout == settings.server_selection_timeout_ms / 1000
        )
        assert client.options.pool_options.socket_timeout == settings.socket_timeout_ms / 1000
    finally:
        await client.close()
    options = transaction_options(settings)
    assert options.read_concern is not None and options.read_concern.level == "snapshot"
    assert options.write_concern is not None and options.write_concern.document == {"w": "majority"}
    assert options.read_preference == ReadPreference.PRIMARY
    assert options.max_commit_time_ms == settings.max_commit_time_ms


def test_receipt_bson_mapping_round_trip_and_binary_types() -> None:
    original = receipt(version=2)
    document = receipt_to_document(original)
    assert document["_id"] == original.projection_task_id
    assert "event_artifact" not in document  # None не зберігається
    encoded = BSON.encode(document, codec_options=STANDARD)
    raw: dict[str, Any] = BSON(encoded).decode(
        codec_options=CodecOptions(uuid_representation=UuidRepresentation.UNSPECIFIED)
    )
    assert isinstance(raw["_id"], Binary) and raw["_id"].subtype == 4  # UUID subtype 4
    assert isinstance(raw["event_bytes"], bytes)  # BSON Binary subtype 0
    decoded: dict[str, Any] = BSON(encoded).decode(codec_options=STANDARD)
    assert receipt_from_document(decoded) == original


def test_not_applied_receipt_has_no_event_fields() -> None:
    document = receipt_to_document(receipt(applied=False, changed=False))
    assert not {"event_bytes", "event_id", "event_sha256", "event_media_type"} & set(document)


async def test_repositories_validate_arguments_before_io() -> None:
    db: Any = object()  # звернення до БД до перевірки аргументів дало б AttributeError
    with pytest.raises(ValueError, match="current collection"):
        await repositories.get_current(db, "entity_projection_versions", uuid4())
    for limit in (0, repositories.MAX_PAGE_SIZE + 1):
        with pytest.raises(ValueError, match="limit"):
            await repositories.list_receipts(db, limit=limit)
