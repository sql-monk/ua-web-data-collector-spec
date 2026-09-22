"""CLI `db migrate`/`db roles` без БД: конфіг-помилки → exit 1, help, відсутній SQL-файл."""

from __future__ import annotations

from pathlib import Path

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


def test_db_roles_translates_driver_errors_without_traceback() -> None:
    """L-3: помилки asyncpg (у т. ч. під час connect/auth) не виходять tracebackом."""
    env = {"COLLECTOR_POSTGRES_DSN": "postgresql://nobody:x@127.0.0.1:1/void"}
    result = runner.invoke(app, ["db", "roles"], env=env)
    assert result.exit_code == 1
    assert "postgres error" in result.output
    assert "Traceback" not in result.output


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
