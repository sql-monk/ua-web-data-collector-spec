"""§9.4 сумісність: fixture-документи попередніх minor-версій валідуються; breaking = fail."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from collector.contracts._base import VersionedDocument, parse_schema_version
from collector.contracts.schema_export import (
    EXPORTED_CONTRACTS,
    ExportedContract,
    build_schema,
    check_compatibility,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "contracts" / "documents"
FIXTURE_NAME = re.compile(r"^(?P<name>[a-z_]+)\.v(?P<major>\d+)\.(?P<minor>\d+)\.json$")
BY_NAME: dict[str, ExportedContract] = {c.name: c for c in EXPORTED_CONTRACTS}
VERSIONED = [c for c in EXPORTED_CONTRACTS if issubclass(c.model, VersionedDocument)]


def fixture_files() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def test_every_versioned_document_has_a_fixture() -> None:
    fixture_names = {FIXTURE_NAME.match(p.name)["name"] for p in fixture_files()}  # type: ignore[index]
    missing = sorted(c.name for c in VERSIONED if c.name not in fixture_names)
    assert missing == [], f"немає fixture для: {missing}"


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.name)
def test_fixture_validates_against_current_model(path: Path) -> None:
    match = FIXTURE_NAME.match(path.name)
    assert match, f"назва fixture має бути <name>.v<major>.<minor>.json: {path.name}"
    contract = BY_NAME[match["name"]]
    major, minor = parse_schema_version(contract.model.contract_version)
    assert int(match["major"]) == major, "fixture іншого major: потрібна migration, не compat-тест"
    assert int(match["minor"]) <= minor, "fixture новішого minor за модель"
    text = path.read_text(encoding="utf-8")
    document = contract.model.model_validate_json(text)
    assert document.schema_version == f"{match['major']}.{match['minor']}"
    # round-trip: модель → JSON → модель без втрат
    assert contract.model.model_validate_json(document.model_dump_json(by_alias=True)) == document


@pytest.mark.parametrize("contract", VERSIONED, ids=lambda c: c.name)
def test_model_rejects_other_major(contract: ExportedContract) -> None:
    major, _ = parse_schema_version(contract.model.contract_version)
    path = FIXTURE_DIR / f"{contract.name}.v{contract.model.contract_version}.json"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = f"{major + 1}.0"
    with pytest.raises(ValueError, match="несумісна"):
        contract.model.model_validate(payload)
    payload["schema_version"] = f"{major}.99"
    with pytest.raises(ValueError, match="minor"):
        contract.model.model_validate(payload)


# --- check_compatibility: правила §9.4 на синтетичних схемах ---------------------------------


def schema(props: dict[str, Any], required: list[str], **extra: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, **extra}


def test_adding_optional_field_is_compatible() -> None:
    old = schema({"a": {"type": "string"}}, ["a"])
    new = schema({"a": {"type": "string"}, "b": {"type": "integer", "default": 0}}, ["a"])
    assert check_compatibility(old, new) == []


def test_removing_or_renaming_field_is_breaking() -> None:
    old = schema({"a": {"type": "string"}, "b": {"type": "integer"}}, ["a"])
    removed = schema({"a": {"type": "string"}}, ["a"])
    renamed = schema({"a": {"type": "string"}, "c": {"type": "integer"}}, ["a"])
    assert check_compatibility(old, removed) == ["<root>.b: property видалено або перейменовано"]
    assert check_compatibility(old, renamed) == ["<root>.b: property видалено або перейменовано"]


def test_new_required_or_type_change_is_breaking() -> None:
    old = schema({"a": {"type": "string"}}, ["a"])
    new_required = schema({"a": {"type": "string"}, "b": {"type": "integer"}}, ["a", "b"])
    type_change = schema({"a": {"type": "integer"}}, ["a"])
    assert check_compatibility(old, new_required) == ["<root>: нові required поля ['b']"]
    assert check_compatibility(old, type_change) == ["<root>.a: змінено тип/обмеження"]
    # description/title/default — не структурні, зміна не breaking
    described = schema({"a": {"type": "string", "description": "x", "title": "A"}}, ["a"])
    assert check_compatibility(old, described) == []


def test_enum_value_removal_and_defs_removal_are_breaking() -> None:
    old = schema(
        {"s": {"$ref": "#/$defs/State"}},
        ["s"],
        **{"$defs": {"State": {"enum": ["a", "b"], "type": "string"}, "Gone": {"type": "object"}}},
    )
    new = schema(
        {"s": {"$ref": "#/$defs/State"}},
        ["s"],
        **{"$defs": {"State": {"enum": ["a", "b", "c"], "type": "string"}}},
    )
    assert check_compatibility(old, new) == ["Gone: визначення видалено"]
    narrowed = schema(
        {"s": {"$ref": "#/$defs/State"}},
        ["s"],
        **{"$defs": {"State": {"enum": ["a"], "type": "string"}, "Gone": {"type": "object"}}},
    )
    assert check_compatibility(old, narrowed) == ["State: видалені enum-значення ['b']"]


@pytest.mark.parametrize("contract", EXPORTED_CONTRACTS, ids=lambda c: c.name)
def test_repository_snapshot_is_compatible_with_current_model(contract: ExportedContract) -> None:
    snapshot = json.loads(
        (REPO_ROOT / "schemas" / contract.relative_path).read_text(encoding="utf-8")
    )
    assert check_compatibility(snapshot, build_schema(contract)) == []


def test_removing_field_from_real_model_snapshot_is_detected() -> None:
    contract = BY_NAME["current_document_base"]
    current = build_schema(contract)
    mutated = json.loads(json.dumps(current))
    del mutated["properties"]["identity_hash"]
    mutated["required"].remove("identity_hash")
    problems = check_compatibility(current, mutated)
    assert problems == ["<root>.identity_hash: property видалено або перейменовано"]
