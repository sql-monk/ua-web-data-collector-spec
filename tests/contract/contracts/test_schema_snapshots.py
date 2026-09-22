"""Рівень 2 Contract (§9.4): snapshot-и `schemas/**` збігаються з моделями (drift = fail)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.contracts._base import VersionedDocument, parse_schema_version
from collector.contracts.schema_export import (
    EXPORTED_CONTRACTS,
    ExportedContract,
    build_schema,
    check_schemas,
    render_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = REPO_ROOT / "schemas"


def test_repository_snapshots_have_no_drift() -> None:
    assert SCHEMAS_DIR.is_dir(), "schemas/ відсутній: виконайте `uv run collector contracts export`"
    problems = check_schemas(SCHEMAS_DIR)
    assert problems == [], "\n".join(problems) + "\n→ `uv run collector contracts export`"


@pytest.mark.parametrize("contract", EXPORTED_CONTRACTS, ids=lambda c: c.relative_path.as_posix())
def test_snapshot_exists_and_is_self_consistent(contract: ExportedContract) -> None:
    path = SCHEMAS_DIR / contract.relative_path
    assert path.is_file()
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    major, _ = parse_schema_version(contract.model.contract_version)
    assert snapshot["x-contract-version"] == contract.model.contract_version
    assert snapshot["$id"].endswith(f"/{contract.group}/{contract.name}.v{major}.json")
    assert snapshot["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert snapshot.get("additionalProperties") is False  # extra="forbid" у всіх контрактах
    assert render_schema(build_schema(contract)) == render_schema(snapshot)


def test_every_versioned_document_declares_schema_version_in_snapshot() -> None:
    for contract in EXPORTED_CONTRACTS:
        if issubclass(contract.model, VersionedDocument):
            schema = build_schema(contract)
            assert (
                schema["properties"]["schema_version"]["default"] == contract.model.contract_version
            )


def test_render_is_deterministic() -> None:
    first = [render_schema(build_schema(c)) for c in EXPORTED_CONTRACTS]
    second = [render_schema(build_schema(c)) for c in EXPORTED_CONTRACTS]
    assert first == second
    assert all(text.endswith("\n") and "\r" not in text for text in first)


def test_exported_names_unique() -> None:
    paths = [c.relative_path for c in EXPORTED_CONTRACTS]
    assert len(set(paths)) == len(paths)
    models = [c.model for c in EXPORTED_CONTRACTS]
    assert len(set(models)) == len(models)
