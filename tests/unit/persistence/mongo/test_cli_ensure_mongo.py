"""`collector db ensure-mongo --validators --indexes --users`: exit codes і відсутність секретів.

Фейковий `pymongo.MongoClient` (RS уже ініціалізовано) і підмінений `apply_mongo_schema`; реальний
шлях проти mongod — tests/integration/mongo/test_cli_ensure_mongo.py.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import pymongo
import pytest
from pymongo.errors import OperationFailure
from typer.testing import CliRunner

from collector.cli import app
from collector.persistence.mongo import admin
from collector.persistence.mongo.migrations import InvalidDocumentsError, MigrationDriftError
from collector.persistence.mongo.schema import IndexReport
from collector.persistence.mongo.users import USER_COMPONENTS, MongoUserCredential

runner = CliRunner()


class _Admin:
    def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if name == "replSetGetStatus":
            return {"set": "rs0"}
        if name == "hello":
            return {"isWritablePrimary": True, "setName": "rs0"}
        raise AssertionError(name)


class _Client:
    created = 0

    def __init__(self, **kwargs: Any) -> None:
        _Client.created += 1
        self.admin = _Admin()
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def root_password(monkeypatch: pytest.MonkeyPatch) -> str:
    password = secrets.token_hex(8)
    _Client.created = 0
    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_USERNAME", "collector_root")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_PASSWORD", password)
    monkeypatch.setenv("COLLECTOR_MONGO_HOST", "mongo")
    monkeypatch.setenv("COLLECTOR_MONGO_PORT", "27017")
    monkeypatch.setenv("COLLECTOR_MONGO_DATABASE", "collector_unit")
    return password


def _patch_apply(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _apply(client: Any, database: str, **kwargs: Any) -> admin.SchemaResult:
        calls.append({"database": database, **kwargs})
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(admin, "apply_mongo_schema", _apply)
    return calls


def test_users_reads_secrets_before_connecting(root_password: str, tmp_path: Path) -> None:
    result = runner.invoke(app, ["db", "ensure-mongo", "--users", "--secrets-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "mongo_uri_projector" in result.stderr
    assert _Client.created == 0, "секрети перевіряються до з'єднання з Mongo"


def test_users_passes_credentials_and_prints_summary(
    root_password: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passwords = {c: secrets.token_hex(8) for c in USER_COMPONENTS}
    for component, password in passwords.items():
        (tmp_path / f"mongo_uri_{component}").write_text(
            f"mongodb://collector_{component}:{password}@mongo:27017/?authSource=admin",
            encoding="utf-8",
        )
    report = IndexReport(created=["a.ix"], present=["b.ix"], extra=["c.ix_old (ops=0)"])
    result_obj = admin.SchemaResult(
        migrations_applied=["0001_x"], indexes=report, users=["collector_projector"]
    )
    calls = _patch_apply(monkeypatch, result_obj)
    argv = ["db", "ensure-mongo", "--validators", "--indexes", "--users"]
    result = runner.invoke(app, [*argv, "--secrets-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls[0]["database"] == "collector_unit"
    assert calls[0]["credentials"] == [
        MongoUserCredential(c, passwords[c]) for c in USER_COMPONENTS
    ]
    assert "mongo migrations applied to collector_unit: 0001_x" in result.stdout
    assert "index created: a.ix" in result.stdout
    assert "c.ix_old" in result.stderr  # зайвий index — лише звіт
    assert "mongo users applied: collector_projector" in result.stdout
    for secret in (root_password, *passwords.values()):
        assert secret not in result.output


@pytest.mark.parametrize(
    "error",
    [
        MigrationDriftError("0001_x: модуль змінено після застосування (checksum не збігається)"),
        InvalidDocumentsError("validationAction=error відхилено: невалідні документи (a=1)"),
        admin.IndexConflictError("a.ux: ключі/unique відрізняються від маніфесту"),
        OperationFailure("not authorized on collector to execute command", code=13),
    ],
)
def test_schema_errors_exit_1_without_traceback_or_secrets(
    root_password: str, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _patch_apply(monkeypatch, error)
    result = runner.invoke(app, ["db", "ensure-mongo", "--validators", "--indexes"])
    assert result.exit_code == 1
    assert "ensure-mongo:" in result.stderr
    assert "Traceback" not in result.output
    assert root_password not in result.output


def test_without_flags_schema_is_not_touched(
    root_password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_apply(monkeypatch, admin.SchemaResult())
    result = runner.invoke(app, ["db", "ensure-mongo"])
    assert result.exit_code == 0, result.output
    assert calls == []
