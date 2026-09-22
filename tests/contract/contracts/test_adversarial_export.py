"""Adversarial рівень 2 Contract: підміна одного snapshot → `--check` червоний; чистота імпорту."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from collector.cli import app
from collector.contracts.schema_export import EXPORTED_CONTRACTS, check_schemas

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = REPO_ROOT / "schemas"
runner = CliRunner()


def copy_schemas(tmp_path: Path) -> Path:
    target = tmp_path / "schemas"
    shutil.copytree(SCHEMAS_DIR, target)
    assert check_schemas(target) == []
    return target


@pytest.mark.parametrize(
    "mutation",
    ["drop_required", "rename_property", "change_type", "bump_version", "add_optional_property"],
)
def test_single_snapshot_tamper_turns_check_red(tmp_path: Path, mutation: str) -> None:
    target = copy_schemas(tmp_path)
    path = target / "releases" / "release_manifest.v1.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "drop_required":
        schema["required"].remove("git_commit")
    elif mutation == "rename_property":
        schema["properties"]["tag_renamed"] = schema["properties"].pop("tag")
    elif mutation == "change_type":
        schema["properties"]["owner"] = {"type": "integer"}
    elif mutation == "bump_version":
        schema["x-contract-version"] = "1.1"
    else:
        schema["properties"]["extra_note"] = {"type": "string"}
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert check_schemas(target) == ["drift: releases/release_manifest.v1.json"]
    result = runner.invoke(app, ["contracts", "export", "--check", "--output", str(target)])
    assert result.exit_code == 1, result.output
    assert "drift: releases/release_manifest.v1.json" in result.output
    assert "schema drift: 1" in result.output


def test_whitespace_only_change_is_still_drift(tmp_path: Path) -> None:
    """Snapshot — точний текст (indent 2, sorted keys); інший формат того ж JSON = drift."""
    target = copy_schemas(tmp_path)
    path = target / "common" / "money.v1.json"
    compact = json.dumps(json.loads(path.read_text(encoding="utf-8")), separators=(",", ":"))
    path.write_text(compact + "\n", encoding="utf-8")
    assert check_schemas(target) == ["drift: common/money.v1.json"]


def test_every_public_model_has_exactly_one_snapshot_on_disk() -> None:
    on_disk = sorted(p.relative_to(SCHEMAS_DIR).as_posix() for p in SCHEMAS_DIR.rglob("*.json"))
    expected = sorted(c.relative_path.as_posix() for c in EXPORTED_CONTRACTS)
    assert on_disk == expected
    assert len(expected) == 32


PROBE_IMPORT_IS_PURE = r"""
import builtins, io, json, pathlib, sys
opened = []
real_open = builtins.open
def spy(file, *args, **kwargs):
    opened.append(str(file))
    return real_open(file, *args, **kwargs)
builtins.open = spy
real_path_open = pathlib.Path.open
def path_spy(self, *args, **kwargs):
    opened.append(str(self))
    return real_path_open(self, *args, **kwargs)
pathlib.Path.open = path_spy
before = set(sys.modules)
import collector.contracts
import collector.contracts.schema_export
import collector.contracts.source_registry
import collector.contracts.values
loaded = sorted(set(sys.modules) - before)
# site-packages/*.dist-info — importlib.metadata entry-point discovery (pydantic plugins),
# не дані проєкту; усе інше (yaml, json, fixtures) — заборонене I/O при імпорті.
data_files = [
    p
    for p in opened
    if not p.endswith((".py", ".pyc", ".zip", ".pth"))
    and "site-packages" not in p
    and ".dist-info" not in p
]
print(json.dumps({"loaded": loaded, "opened": data_files}))
"""


def test_import_contracts_reads_no_data_files_and_no_lazy_deps() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE_IMPORT_IS_PURE],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    probe = json.loads(result.stdout)
    tops = {name.split(".")[0] for name in probe["loaded"]}
    assert tops.isdisjoint({"yaml", "phonenumbers", "idna", "httpx", "sqlalchemy", "pymongo"}), tops
    assert probe["opened"] == [], f"імпорт читає файли з диска: {probe['opened']}"


PROBE_REGISTRY_READ_ONCE = r"""
import json, pathlib
opened = []
real_path_open = pathlib.Path.open
def path_spy(self, *args, **kwargs):
    if self.name == "source-registry.yaml":
        opened.append(str(self))
    return real_path_open(self, *args, **kwargs)
pathlib.Path.open = path_spy
from collector.contracts import SourceIdentity
SourceIdentity(source_id="catalog_ua_rozetka", source_item_id="1")
after_first = len(opened)
for i in range(50):
    SourceIdentity(source_id="catalog_ua_rozetka", source_item_id=str(i))
print(json.dumps({"first": after_first, "total": len(opened)}))
"""


def test_registry_is_read_once_on_first_validation_only() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE_REGISTRY_READ_ONCE],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    probe = json.loads(result.stdout)
    assert probe == {"first": 1, "total": 1}
