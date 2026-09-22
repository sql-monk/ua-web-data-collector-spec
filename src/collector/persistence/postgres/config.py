"""Конфігурація підключення до PostgreSQL: DSN з env, secret через `*_FILE` (§13).

- `COLLECTOR_POSTGRES_DSN` — повний DSN (`postgresql://user:pass@host:5432/db`);
- `COLLECTOR_POSTGRES_DSN_FILE` — шлях до файлу з DSN (Docker secrets); має пріоритет над
  inline-значенням, щоб secret не потрапляв у `docker inspect`/env-дамп.

DSN нормалізується до драйвера asyncpg (`postgresql+asyncpg://`); `postgresql+psycopg*`
відхиляється явно — runtime підтримує лише asyncpg.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

DSN_ENV = "COLLECTOR_POSTGRES_DSN"
DSN_FILE_ENV = "COLLECTOR_POSTGRES_DSN_FILE"
ASYNC_DRIVER = "postgresql+asyncpg"


class PostgresConfigError(ValueError):
    """Відсутній або невалідний DSN."""


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    """Розібраний DSN; `url` уже з драйвером asyncpg."""

    url: URL

    @property
    def redacted_dsn(self) -> str:
        """DSN без пароля — для логів і повідомлень CLI."""
        return self.url.render_as_string(hide_password=True)

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresSettings:
        return cls(url=normalize_async_url(dsn))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> PostgresSettings:
        """Читає `COLLECTOR_POSTGRES_DSN_FILE`, інакше `COLLECTOR_POSTGRES_DSN`."""
        env = os.environ if environ is None else environ
        dsn_file = env.get(DSN_FILE_ENV)
        if dsn_file:
            path = Path(dsn_file)
            try:
                dsn = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                msg = f"{DSN_FILE_ENV}={dsn_file!r}: не вдалося прочитати файл ({exc})"
                raise PostgresConfigError(msg) from exc
            if not dsn:
                msg = f"{DSN_FILE_ENV}={dsn_file!r}: файл порожній"
                raise PostgresConfigError(msg)
            return cls.from_dsn(dsn)
        dsn = env.get(DSN_ENV, "").strip()
        if not dsn:
            msg = f"не задано {DSN_ENV} (або {DSN_FILE_ENV})"
            raise PostgresConfigError(msg)
        return cls.from_dsn(dsn)


def normalize_async_url(dsn: str) -> URL:
    """`postgresql://` або `postgres://` → `postgresql+asyncpg://`; інший драйвер — помилка."""
    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError) as exc:
        msg = "невалідний PostgreSQL DSN"
        raise PostgresConfigError(msg) from exc
    if url.drivername in {"postgresql", "postgres"}:
        url = url.set(drivername=ASYNC_DRIVER)
    if url.drivername != ASYNC_DRIVER:
        msg = f"підтримується лише драйвер asyncpg, отримано {url.drivername!r}"
        raise PostgresConfigError(msg)
    if not url.database:
        msg = "DSN має містити назву бази даних"
        raise PostgresConfigError(msg)
    return url
