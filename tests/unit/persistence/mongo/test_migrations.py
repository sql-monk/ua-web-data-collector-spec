"""Раннер Mongo-міграцій без БД: пошук, порядок, checksum (drift-детекція)."""

from __future__ import annotations

from pathlib import Path

import pytest

from collector.persistence.mongo.migrations import (
    MIGRATIONS_DIR_ENV,
    MigrationDriftError,
    MigrationsNotFoundError,
    discover,
    find_migrations_dir,
    load_asset,
    migration_checksum,
)

REPO_MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations" / "mongo"
UPGRADE = "def upgrade(db):\n    pass\n"


def _module(root: Path, name: str, body: str = UPGRADE) -> Path:
    path = root / name
    path.write_bytes(body.encode())
    return path


def test_repo_migrations_are_ordered_forward_only_modules() -> None:
    found = discover(REPO_MIGRATIONS)
    assert [m.version for m in found] == ["0001", "0002"]
    for migration in found:
        module = migration.load()
        assert callable(module.upgrade)
        assert not hasattr(module, "downgrade"), "downgrade не підтримується (forward-only)"


def test_checksum_ignores_line_endings_but_not_content(tmp_path: Path) -> None:
    lf = _module(tmp_path, "0001_a.py")
    crlf_dir = tmp_path / "crlf"
    crlf_dir.mkdir()
    crlf = _module(crlf_dir, "0001_a.py", UPGRADE.replace("\n", "\r\n"))
    assert migration_checksum(lf) == migration_checksum(crlf)
    changed = _module(crlf_dir, "0001_a.py", "def upgrade(db):\n    return None\n")
    assert migration_checksum(changed) != migration_checksum(lf)


def test_checksum_covers_assets(tmp_path: Path) -> None:
    module = _module(tmp_path, "0001_a.py")
    before = migration_checksum(module)
    assets = tmp_path / "0001_a"
    assets.mkdir()
    (assets / "v.json").write_text('{"a": 1}\n', encoding="utf-8")
    with_asset = migration_checksum(module)
    assert with_asset != before
    (assets / "v.json").write_text('{"a": 2}\n', encoding="utf-8")
    assert migration_checksum(module) != with_asset
    assert load_asset(str(module), "v") == {"a": 2}


def test_discover_skips_non_migrations_and_rejects_duplicate_versions(tmp_path: Path) -> None:
    _module(tmp_path, "0002_b.py")
    _module(tmp_path, "0001_a.py")
    _module(tmp_path, "helpers.py")
    _module(tmp_path, "1_bad.py")
    assert [m.name for m in discover(tmp_path)] == ["a", "b"]
    _module(tmp_path, "0001_again.py")
    with pytest.raises(MigrationDriftError, match="0001"):
        discover(tmp_path)


def test_module_without_upgrade_is_rejected(tmp_path: Path) -> None:
    _module(tmp_path, "0001_a.py", "X = 1\n")
    with pytest.raises(MigrationDriftError, match="upgrade"):
        discover(tmp_path)[0].load()


def test_find_migrations_dir_env_and_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MIGRATIONS_DIR_ENV, str(tmp_path))
    assert find_migrations_dir() == tmp_path
    monkeypatch.setenv(MIGRATIONS_DIR_ENV, str(tmp_path / "missing"))
    with pytest.raises(MigrationsNotFoundError):
        find_migrations_dir()
    monkeypatch.delenv(MIGRATIONS_DIR_ENV)
    nested = REPO_MIGRATIONS.parent.parent / "src" / "collector"
    assert find_migrations_dir(nested).resolve() == REPO_MIGRATIONS.resolve()
