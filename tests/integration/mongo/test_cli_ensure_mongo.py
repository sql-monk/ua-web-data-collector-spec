"""`collector db ensure-mongo --validators --indexes [--users]` проти RS (контракт §16.2).

CLI під `CliRunner`: той самий root-логін, що в compose one-shot `ensure-mongo`, env-адреса —
loopback mapped port тестового mongod. Секрети (root-пароль, `mongo_uri_*`) — лише з рантайму.
"""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from typer.testing import CliRunner

from collector.cli import app
from collector.persistence.mongo.migrations import MIGRATIONS_DIR_ENV
from collector.persistence.mongo.schema import DOMAIN_COLLECTIONS, MIGRATIONS_COLLECTION
from collector.persistence.mongo.users import USER_COMPONENTS, user_name

pytestmark = pytest.mark.integration

runner = CliRunner()
REPO_MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations" / "mongo"


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, mongo_server: Any, mongo_db: Database[Any]) -> str:
    monkeypatch.setenv("COLLECTOR_MONGO_HOST", mongo_server.host)
    monkeypatch.setenv("COLLECTOR_MONGO_PORT", str(mongo_server.port))
    monkeypatch.setenv("COLLECTOR_MONGO_REPLICA_SET", "rs0")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_USERNAME", mongo_server.username)
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_PASSWORD", mongo_server.password)
    monkeypatch.delenv("COLLECTOR_MONGO_ROOT_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("COLLECTOR_MONGO_DATABASE", mongo_db.name)
    monkeypatch.delenv(MIGRATIONS_DIR_ENV, raising=False)
    return str(mongo_server.password)


def _schema(db: Database[Any]) -> dict[str, Any]:
    return {
        info["name"]: (info.get("options", {}), sorted(db[info["name"]].index_information()))
        for info in db.list_collections()
        if info["name"] != MIGRATIONS_COLLECTION
    }


def test_cli_validators_indexes_is_idempotent(cli_env: str, mongo_db: Database[Any]) -> None:
    first = runner.invoke(app, ["db", "ensure-mongo", "--validators", "--indexes"])
    assert first.exit_code == 0, first.output
    assert "0001_collections_validators_warn, 0002_validators_error" in first.stdout
    state = _schema(mongo_db)
    assert set(DOMAIN_COLLECTIONS) <= set(state)
    second = runner.invoke(app, ["db", "ensure-mongo", "--validators", "--indexes"])
    assert second.exit_code == 0, second.output
    assert "none (up to date)" in second.stdout
    assert "created=0" in second.stdout
    assert _schema(mongo_db) == state
    assert cli_env not in first.output + second.output


def test_cli_drift_exits_1_without_secrets(
    cli_env: str, mongo_db: Database[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = tmp_path / "mongo"
    shutil.copytree(REPO_MIGRATIONS, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv(MIGRATIONS_DIR_ENV, str(copy))
    assert runner.invoke(app, ["db", "ensure-mongo", "--validators"]).exit_code == 0
    module = next(copy.glob("0001_*.py"))
    module.write_text(module.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    result = runner.invoke(app, ["db", "ensure-mongo", "--validators"])
    assert result.exit_code == 1
    assert "checksum" in result.stderr
    assert "Traceback" not in result.output
    assert cli_env not in result.output


def test_cli_users_creates_component_logins(
    cli_env: str,
    mongo_server: Any,
    mongo_db: Database[Any],
    mongo_root: MongoClient[dict[str, Any]],
    tmp_path: Path,
    mongo_users_cleanup: None,
) -> None:
    passwords = {c: secrets.token_hex(12) for c in USER_COMPONENTS}
    for component, password in passwords.items():
        uri = (
            f"mongodb://{user_name(component)}:{quote_plus(password)}@mongo:27017/"
            "?replicaSet=rs0&authSource=admin"
        )
        (tmp_path / f"mongo_uri_{component}").write_text(uri + "\n", encoding="utf-8")
    argv = ["db", "ensure-mongo", "--validators", "--indexes", "--users"]
    for _ in range(2):  # ідемпотентно
        result = runner.invoke(app, [*argv, "--secrets-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert all(p not in result.output for p in passwords.values())
    assert "collector_projector" in result.stdout
    client: MongoClient[dict[str, Any]] = MongoClient(
        mongo_server.uri("collector_api_ro", passwords["api_ro"]), serverSelectionTimeoutMS=5_000
    )
    try:
        assert client.get_database(mongo_db.name)["sellers_current"].count_documents({}) == 0
    finally:
        client.close()


def test_cli_users_missing_secret_changes_nothing(
    cli_env: str, mongo_db: Database[Any], tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["db", "ensure-mongo", "--validators", "--users", "--secrets-dir", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "mongo_uri_projector" in result.stderr
    assert mongo_db.list_collection_names() == []  # секрети читаються до з'єднання
