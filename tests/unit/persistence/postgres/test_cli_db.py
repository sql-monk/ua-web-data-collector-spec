"""CLI `db migrate`/`db roles` без БД: конфіг-помилки → exit 1, help, відсутній SQL-файл."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import asyncpg
import pytest
from typer.testing import CliRunner

from collector.cli import app

runner = CliRunner()


def test_db_migrate_without_dsn_exits_1_with_config_message() -> None:
    result = runner.invoke(app, ["db", "migrate"], env={"COLLECTOR_POSTGRES_DSN": ""})
    assert result.exit_code == 1
    assert "postgres config" in result.output
    assert "COLLECTOR_POSTGRES_DSN" in result.output
    assert "not implemented" not in result.output


def test_db_roles_with_missing_sql_file_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["db", "roles", "--sql", str(tmp_path / "nope.sql")],
        env={"COLLECTOR_POSTGRES_DSN": "postgresql://u:p@127.0.0.1:1/db"},
    )
    assert result.exit_code == 1
    assert "не знайдено" in result.output


def test_db_migrate_help_documents_check_and_partitions() -> None:
    result = runner.invoke(app, ["db", "migrate", "--help"])
    assert result.exit_code == 0
    assert "--check" in result.output
    assert "--partitions-ahead" in result.output
    assert "COLLECTOR_POSTGRES_DSN" in result.output


def test_db_roles_help_mentions_sql_option() -> None:
    result = runner.invoke(app, ["db", "roles", "--help"])
    assert result.exit_code == 0
    assert "--sql" in result.output


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.InvalidPasswordError("password authentication failed"),
        ConnectionRefusedError("connect call failed"),
    ],
    ids=["asyncpg-auth", "os-connect"],
)
def test_db_roles_translates_driver_errors_without_traceback(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """L-3: помилки драйвера (connect/auth) не виходять tracebackом.

    Помилка підставляється у `asyncpg.connect` (SQLAlchemy бере функцію з модуля на кожен
    виклик), тому тест не створює жодного socket і поводиться однаково на Linux і Windows:
    на POSIX `pytest-socket` блокує саме створення socket, і спроба реального зʼєднання з
    закритим портом давала б `SocketBlockedError` замість перевіреної поведінки (CI PR #3).
    """

    async def _raise(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(asyncpg, "connect", _raise)
    env = {"COLLECTOR_POSTGRES_DSN": "postgresql://nobody:pw@postgres.invalid:5432/void"}
    result = runner.invoke(app, ["db", "roles"], env=env)
    assert result.exit_code == 1
    assert "postgres error" in result.output
    assert "Traceback" not in result.output
    assert "pw" not in result.output.replace("password", "")  # пароль не витікає у вивід


def test_postgres_error_predicate_covers_driver_and_sqlalchemy_errors() -> None:
    """Предикат `_is_postgres_error` не ковтає сторонні винятки (напр. помилки коду)."""
    import asyncpg
    from sqlalchemy.exc import OperationalError

    from collector.cli import _is_postgres_error

    assert _is_postgres_error(asyncpg.exceptions.InvalidPasswordError("nope"))
    assert _is_postgres_error(OperationalError("s", None, Exception("x")))
    assert _is_postgres_error(ConnectionRefusedError())
    assert not _is_postgres_error(ValueError("bug у коді"))
    assert not _is_postgres_error(KeyError("bug у коді"))


def test_importing_cli_does_not_pull_heavy_database_stack() -> None:
    """`collector version`/`--help`/`worker <role>` не мають тягнути SQLAlchemy (CI PR #3).

    WP-00 PR2 навмисно тримає pymongo/fastapi/uvicorn у тілах команд: image HEALTHCHECK — це
    `collector version`, а `collector worker <role>` стартує в 11 контейнерах одночасно. Коли
    WP-01A додав `from sqlalchemy.exc import …` і `PostgresSettings` у модуль `collector.cli`,
    кожен старт подорожчав на ~0.3 с CPU, і на 2-ядерному runner-і healthcheck-и workers
    почали падати за таймаутом (`container collector-fetch-worker-1 is unhealthy`).

    Перевірка — у чистому інтерпретаторі: `sys.modules` тесту вже містить майже все.
    """
    probe = (
        "import collector.cli, sys; "
        "print(','.join(sorted(m for m in ('sqlalchemy', 'asyncpg', 'alembic', 'pymongo', "
        "'fastapi', 'uvicorn') if m in sys.modules)))"
    )
    result = subprocess.run(  # noqa: S603 — фіксований argv без shell
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"важкі імпорти у collector.cli: {result.stdout.strip()}"
