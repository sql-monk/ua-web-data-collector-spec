"""CLI `collector db migrate [--check]` і `collector db roles` проти порожньої БД
(DSN через env і через `COLLECTOR_POSTGRES_DSN_FILE`).

Тести синхронні: CLI сам викликає `asyncio.run` (async-фікстуру БД pytest-asyncio виконує у
власному loop до старту тесту)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from collector.cli import app
from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.migrations import head_revision

pytestmark = pytest.mark.integration

runner = CliRunner()


def _dsn(settings: PostgresSettings) -> str:
    return settings.url.render_as_string(hide_password=False)


def test_db_migrate_check_and_roles_from_env(pg_empty_database: PostgresSettings) -> None:
    env = {"COLLECTOR_POSTGRES_DSN": _dsn(pg_empty_database)}
    check_before = runner.invoke(app, ["db", "migrate", "--check"], env=env)
    assert check_before.exit_code == 1, check_before.output
    assert "schema drift" in check_before.output

    migrate = runner.invoke(app, ["db", "migrate", "--partitions-ahead", "1"], env=env)
    assert migrate.exit_code == 0, migrate.output
    assert f"empty -> {head_revision()}" in migrate.output
    assert migrate.output.count("partition created: audit_log_y") == 2
    assert pg_empty_database.url.password not in migrate.output

    check_after = runner.invoke(app, ["db", "migrate", "--check"], env=env)
    assert check_after.exit_code == 0, check_after.output
    assert f"schema up to date: revision={head_revision()}" in check_after.output

    again = runner.invoke(app, ["db", "migrate"], env=env)
    assert again.exit_code == 0, again.output
    assert f"{head_revision()} -> {head_revision()}" in again.output

    roles = runner.invoke(app, ["db", "roles"], env=env)
    assert roles.exit_code == 0, roles.output
    assert "collector_api_ro" in roles.output


def test_db_migrate_reads_dsn_file_over_inline(
    pg_empty_database: PostgresSettings, tmp_path: Path
) -> None:
    dsn_file = tmp_path / "dsn"
    dsn_file.write_text(_dsn(pg_empty_database), encoding="utf-8")
    env = {
        "COLLECTOR_POSTGRES_DSN": "postgresql://nobody:x@127.0.0.1:1/void",
        "COLLECTOR_POSTGRES_DSN_FILE": str(dsn_file),
    }
    result = runner.invoke(app, ["db", "migrate"], env=env)
    assert result.exit_code == 0, result.output
    assert f"-> {head_revision()}" in result.output


def test_db_migrate_unreachable_server_exits_1_without_traceback() -> None:
    env = {"COLLECTOR_POSTGRES_DSN": "postgresql://nobody:x@127.0.0.1:1/void"}
    result = runner.invoke(app, ["db", "migrate"], env=env)
    assert result.exit_code == 1
    assert "postgres error" in result.output
    assert "Traceback" not in result.output
