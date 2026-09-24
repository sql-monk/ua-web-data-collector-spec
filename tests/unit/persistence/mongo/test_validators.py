"""Генерація Mongo `$jsonSchema` зі snapshot-ів WP-01C і заморожені asset-и міграцій."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from collector.persistence.mongo.migrations import discover
from collector.persistence.mongo.validators import (
    RECIPES,
    main,
    mongo_validator,
    render_validator,
)

REPO = Path(__file__).resolve().parents[4]
SNAPSHOTS = REPO / "schemas" / "mongo"
MIGRATIONS = REPO / "migrations" / "mongo"
MONGO_UNSUPPORTED = {"$ref", "$defs", "$schema", "$id", "format", "default", "type", "examples"}


def _keys(node: Any) -> set[str]:
    """Ключі схеми (без імен полів усередині `properties`)."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key == "properties":
                for spec in value.values():
                    found |= _keys(spec)
            else:
                found |= _keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keys(item)
    return found


def _snapshot(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((SNAPSHOTS / name).read_text(encoding="utf-8"))
    return loaded


def test_uuid_datetime_bytes_and_integer_are_mapped_to_bson_types() -> None:
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "format": "uuid", "pattern": "^x$"},
            "at": {"type": "string", "format": "date-time", "minLength": 1},
            "blob": {"anyOf": [{"type": "string", "format": "base64url"}, {"type": "null"}]},
            "n": {"type": "integer", "minimum": 1},
            "x": {"type": "number"},
            "ok": {"type": "boolean"},
        },
        "required": ["id"],
        "additionalProperties": False,
    }
    body = mongo_validator(schema)["$jsonSchema"]
    props = body["properties"]
    assert props["id"] == {"bsonType": "binData"}  # рядкові обмеження відкинуто
    assert props["at"] == {"bsonType": "date"}
    assert props["blob"] == {"anyOf": [{"bsonType": "binData"}, {"bsonType": "null"}]}
    assert props["n"] == {"bsonType": ["int", "long"], "minimum": 1}
    assert props["x"] == {"bsonType": ["int", "long", "double", "decimal"]}
    assert props["ok"] == {"bsonType": "bool"}
    assert body["required"] == ["id"] and body["additionalProperties"] is False


def test_refs_are_inlined_and_recursion_becomes_any() -> None:
    schema = {
        "$defs": {
            "Json": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"$ref": "#/$defs/Json"}},
                ]
            },
            "Src": {"type": "object", "properties": {"id": {"type": "string"}}},
        },
        "type": "object",
        "properties": {
            "src": {"$ref": "#/$defs/Src"},
            "core": {"type": "object", "additionalProperties": {"$ref": "#/$defs/Json"}},
        },
    }
    body = mongo_validator(schema)["$jsonSchema"]
    assert body["properties"]["src"] == {
        "bsonType": "object",
        "properties": {"id": {"bsonType": "string"}},
    }
    json_value = body["properties"]["core"]["additionalProperties"]
    assert json_value["anyOf"][1] == {"bsonType": "array", "items": {}}
    assert not _keys(body) & MONGO_UNSUPPORTED


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ({"type": "string", "format": "email"}, "format"),
        ({"type": "object", "unevaluatedProperties": False}, "unevaluatedProperties"),
        ({"$ref": "https://example.invalid/x.json"}, r"\$ref"),
        ({"$ref": "#/$defs/Missing"}, "Missing"),
        ({"type": "tuple"}, "tuple"),
    ],
)
def test_unsupported_constructs_fail_loudly(schema: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        mongo_validator(schema)


def test_open_top_level_and_extra_properties() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
    }
    open_body = mongo_validator(schema, open_top_level=True)["$jsonSchema"]
    assert "additionalProperties" not in open_body
    closed = mongo_validator(schema, extra_properties={"_id": {"bsonType": "binData"}})
    assert closed["$jsonSchema"]["properties"]["_id"] == {"bsonType": "binData"}
    assert closed["$jsonSchema"]["additionalProperties"] is False


def test_current_validator_keeps_nested_strictness_and_required_fields() -> None:
    body = RECIPES["current_document"].build(_snapshot("current_document_base.v1.json"))
    schema = body["$jsonSchema"]
    assert schema["title"] == "current_document_base@1.0"
    assert "additionalProperties" not in schema  # доменні поля WP-07/WP-09
    assert schema["properties"]["_id"] == {"bsonType": "binData"}
    assert schema["properties"]["schema_version"]["bsonType"] == ["int", "long"]
    assert schema["properties"]["lineage"]["additionalProperties"] is False
    assert schema["properties"]["time"]["properties"]["observed_at"] == {"bsonType": "date"}
    assert set(schema["required"]) >= {"_id", "source", "projection_version", "state_hash"}


def test_receipt_validator_has_uuid_pk_and_binary_event_bytes() -> None:
    body = RECIPES["applied_projection_receipt"].build(
        _snapshot("applied_projection_receipt.v1.json")
    )
    schema = body["$jsonSchema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["_id"] == {"bsonType": "binData"}
    assert schema["properties"]["event_bytes"]["anyOf"][0] == {"bsonType": "binData"}
    assert schema["properties"]["committed_at"] == {"bsonType": "date"}


def _latest_assets() -> dict[str, Path]:
    latest: dict[str, Path] = {}
    for migration in discover(MIGRATIONS):
        if migration.asset_dir.is_dir():
            for asset in sorted(migration.asset_dir.glob("*.json")):
                latest[asset.stem] = asset
    return latest


def test_latest_frozen_assets_match_current_snapshots() -> None:
    """Зміна snapshot-а WP-01C без нової Mongo-міграції — червоний тест (drift validator)."""
    latest = _latest_assets()
    assert set(latest) == set(RECIPES)
    for name, asset in latest.items():
        recipe = RECIPES[name]
        expected = render_validator(recipe.build(_snapshot(recipe.snapshot)))
        actual = asset.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert actual == expected, (
            f"{asset} застарів відносно schemas/mongo/{recipe.snapshot}: додайте нову міграцію "
            f"з `uv run python -m collector.persistence.mongo.validators {name}`"
        )
        assert not _keys(json.loads(actual)) & MONGO_UNSUPPORTED


def test_cli_main_renders_asset(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["applied_projection_receipt", str(SNAPSHOTS)]) == 0
    assert json.loads(capsys.readouterr().out)["$jsonSchema"]["title"].startswith("applied_")
    assert main(["unknown"]) == 2
