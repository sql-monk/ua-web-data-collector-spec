"""`PostgresSettings`: DSN з env, `*_FILE` має пріоритет, нормалізація драйвера, redaction."""

from __future__ import annotations

from pathlib import Path

import pytest

from collector.persistence.postgres.config import (
    DSN_ENV,
    DSN_FILE_ENV,
    PostgresConfigError,
    PostgresSettings,
    normalize_async_url,
)


def test_plain_postgresql_scheme_is_normalized_to_asyncpg() -> None:
    settings = PostgresSettings.from_env({DSN_ENV: "postgresql://u:p@db:5432/collector"})
    assert settings.url.drivername == "postgresql+asyncpg"
    assert settings.url.database == "collector"
    assert settings.redacted_dsn == "postgresql+asyncpg://u:***@db:5432/collector"
    assert "p@" not in settings.redacted_dsn


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql+asyncpg://"])
def test_accepted_schemes(scheme: str) -> None:
    assert normalize_async_url(f"{scheme}u:p@h/db").drivername == "postgresql+asyncpg"


@pytest.mark.parametrize(
    "dsn",
    ["postgresql+psycopg://u:p@h/db", "mysql://u:p@h/db", "postgresql://u:p@h", "not a dsn ://"],
)
def test_rejected_dsn(dsn: str) -> None:
    with pytest.raises(PostgresConfigError):
        normalize_async_url(dsn)


def test_dsn_file_has_priority_and_is_stripped(tmp_path: Path) -> None:
    secret_file = tmp_path / "dsn"
    secret_file.write_text("postgresql://file:pw@h/filedb\n", encoding="utf-8")
    settings = PostgresSettings.from_env(
        {DSN_ENV: "postgresql://env:pw@h/envdb", DSN_FILE_ENV: str(secret_file)}
    )
    assert settings.url.database == "filedb"


def test_missing_env_and_empty_or_absent_file_raise(tmp_path: Path) -> None:
    with pytest.raises(PostgresConfigError, match=DSN_ENV):
        PostgresSettings.from_env({})
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(PostgresConfigError, match="порожній"):
        PostgresSettings.from_env({DSN_FILE_ENV: str(empty)})
    with pytest.raises(PostgresConfigError, match="не вдалося прочитати"):
        PostgresSettings.from_env({DSN_FILE_ENV: str(tmp_path / "missing")})
