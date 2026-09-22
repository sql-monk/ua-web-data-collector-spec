"""Адреси інфраструктурних сервісів і секрети з env/`*_FILE` (Docker secrets).

Спільне для CLI (`db migrate`, `db ensure-mongo`) і health-стаба API; без важких імпортів,
щоб `collector version`/`--help` (image HEALTHCHECK) лишалися дешевими.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


def env_or_file(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Значення `NAME` або вміст файлу з `NAME_FILE` (Docker secrets), без trailing newline.

    Секрет ніколи не потрапляє в логи: викликачі логують лише факт наявності.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    value = env.get(name)
    if value:
        return value
    path = env.get(f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return None


def postgres_address(environ: Mapping[str, str] | None = None) -> tuple[str, int]:
    """Хост/порт PostgreSQL з env (`COLLECTOR_POSTGRES_HOST`/`COLLECTOR_POSTGRES_PORT`)."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    return env.get("COLLECTOR_POSTGRES_HOST", "postgres"), int(
        env.get("COLLECTOR_POSTGRES_PORT", "5432")
    )


def mongo_address(environ: Mapping[str, str] | None = None) -> tuple[str, int]:
    """Хост/порт MongoDB з env (`COLLECTOR_MONGO_HOST`/`COLLECTOR_MONGO_PORT`)."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    return env.get("COLLECTOR_MONGO_HOST", "mongo"), int(env.get("COLLECTOR_MONGO_PORT", "27017"))


def minio_health_url(environ: Mapping[str, str] | None = None) -> str:
    """URL liveness endpoint MinIO (`COLLECTOR_MINIO_URL`, типово `http://minio:9000`)."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    return env.get("COLLECTOR_MINIO_URL", "http://minio:9000").rstrip("/") + "/minio/health/live"
