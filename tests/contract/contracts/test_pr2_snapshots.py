"""PR2 snapshots: те, на що спираються WP-01B (validators) і WP-01A/WP-04 (news/translation)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "schemas"
PR2_MONGO = (
    "entity_projection_version",
    "observation_record",
    "seller_contact_observation",
    "review_question_record",
)


def load(group: str, name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        (SCHEMAS_DIR / group / f"{name}.v1.json").read_text(encoding="utf-8")
    )
    return result


@pytest.mark.parametrize("name", PR2_MONGO)
def test_mongo_records_require_uuid_id_and_schema_version(name: str) -> None:
    schema = load("mongo", name)
    assert "_id" in schema["required"]
    assert schema["properties"]["_id"]["format"] == "uuid"
    assert schema["properties"]["schema_version"]["default"] == "1.0"
    assert schema["additionalProperties"] is False


def test_normalized_payload_is_an_events_snapshot_not_a_mongo_collection() -> None:
    assert (SCHEMAS_DIR / "events" / "normalized_projection_payload.v1.json").is_file()
    assert not (SCHEMAS_DIR / "mongo" / "normalized_projection_payload.v1.json").exists()
    schema = load("events", "normalized_projection_payload")
    assert {"entity_uuid", "entity_kind", "source", "identity_hash", "core", "system_time"} <= set(
        schema["required"]
    )


def test_news_translation_body_is_nullable_and_status_closed() -> None:
    schema = load("common", "news_translation")
    for field in ("body_text", "body_artifact", "title", "lead"):
        assert field not in schema["required"]
    assert schema["$defs"]["TranslationStatus"]["enum"] == [
        "pending",
        "translated",
        "not_required",
        "translation_failed",
    ]


def test_news_version_created_body_artifact_nullable() -> None:
    schema = load("events", "news_version_created")
    assert "cleaned_body_artifact" not in schema["required"]
    assert schema["properties"]["event_type"]["const"] == "news.version_created"
