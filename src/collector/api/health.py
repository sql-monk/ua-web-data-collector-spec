"""Health/readiness стаб `GET /api/v1/health/components` і перевірки компонентів (WP-00 PR2).

Стаб належить WP-00 лише як «щось, що image `collector` запускає для `api`»; повний
operator API (§9.10), OIDC/BFF і решта endpoints — owner WP-11A, який замінює цей модуль.

Що перевіряється (чесно, без драйверів там, де їх ще не обрав owner-WP):

- `postgres` — лише TCP-з'єднання з `COLLECTOR_POSTGRES_HOST:PORT`; credentials/SQL не
  перевіряються (SQLAlchemy/psycopg або asyncpg обирає WP-01A);
- `mongo` — `hello` через PyMongo без автентифікації (команда дозволена без auth):
  компонент `ok` лише коли член є `isWritablePrimary`, тобто replica set ініціалізовано
  (`collector db ensure-mongo`); health не потребує Mongo credentials (§13);
- `minio` — HTTP `GET /minio/health/live` (офіційний liveness endpoint MinIO).

`ready` = усі компоненти `ok`. Контейнер `api` у Compose стартує лише після
`migrate-postgres`/`ensure-mongo` (`depends_on: service_completed_successfully`), тому
readiness додатково не потребує прапорця «міграції виконані»; WP-01A/WP-01B додають
перевірку версії схеми/validators сюди, коли вони з'являться.

Модуль також запускається як `python -m collector.api.health postgres mongo` — це
Docker healthcheck workers/one-shots («process + критична dependency», §7.5): exit 0, якщо всі
названі компоненти `ok`, інакше 1.
"""

from __future__ import annotations

import os
import socket
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Literal

from fastapi import FastAPI, Response
from pydantic import BaseModel, ConfigDict
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from collector.core.version import VersionInfo, version_info

ComponentName = Literal["postgres", "mongo", "minio"]
COMPONENT_NAMES: tuple[ComponentName, ...] = ("postgres", "mongo", "minio")
DEFAULT_TIMEOUT_SECONDS = 3.0
HEALTH_PATH = "/api/v1/health/components"


class ComponentStatus(BaseModel):
    """Стан одного компонента для GUI «Огляд» (§7.7 п.1) і Docker healthcheck."""

    model_config = ConfigDict(frozen=True)

    name: ComponentName
    ok: bool
    latency_ms: float
    detail: str


class HealthReport(BaseModel):
    """Відповідь `GET /api/v1/health/components`."""

    model_config = ConfigDict(frozen=True)

    ready: bool
    components: tuple[ComponentStatus, ...]
    version: VersionInfo


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


def _timed(name: ComponentName, probe: Callable[[], str], *, timeout: float) -> ComponentStatus:
    started = time.perf_counter()
    try:
        detail = probe()
        ok = True
    except (OSError, PyMongoError, ValueError, TimeoutError) as exc:
        detail = f"{type(exc).__name__}: {exc}"[:200]
        ok = False
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    if ok and latency_ms > timeout * 1000:
        # Защитний випадок: probe відповів, але довше за timeout — трактуємо як деградацію.
        return ComponentStatus(name=name, ok=False, latency_ms=latency_ms, detail="slow")
    return ComponentStatus(name=name, ok=ok, latency_ms=latency_ms, detail=detail)


def check_postgres(
    environ: Mapping[str, str] | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> ComponentStatus:
    """TCP-з'єднання з PostgreSQL (без credentials; SQL-перевірку додає WP-01A)."""
    host, port = postgres_address(environ)

    def probe() -> str:
        with socket.create_connection((host, port), timeout=timeout):
            return f"tcp {host}:{port} reachable (no SQL check yet; owner WP-01A)"

    return _timed("postgres", probe, timeout=timeout)


def check_mongo(
    environ: Mapping[str, str] | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> ComponentStatus:
    """`hello` без auth: `ok` лише коли член — writable primary ініціалізованого replica set."""
    host, port = mongo_address(environ)
    timeout_ms = int(timeout * 1000)

    def probe() -> str:
        client: MongoClient[dict[str, object]] = MongoClient(
            host=host,
            port=port,
            directConnection=True,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
        )
        try:
            hello = client.admin.command("hello")
        finally:
            client.close()
        if not hello.get("isWritablePrimary"):
            raise ValueError(
                f"member is not writable primary (setName={hello.get('setName')!r}); "
                "run `collector db ensure-mongo`"
            )
        return f"writable primary of replica set {hello.get('setName')!r}"

    return _timed("mongo", probe, timeout=timeout)


def check_minio(
    environ: Mapping[str, str] | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> ComponentStatus:
    """HTTP liveness endpoint MinIO (`/minio/health/live`)."""
    url = minio_health_url(environ)

    def probe() -> str:
        request = urllib.request.Request(url, method="GET")  # noqa: S310 — лише http(s) URL з env
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            status = exc.code
        if status != 200:
            raise ValueError(f"liveness returned HTTP {status}")
        return "liveness HTTP 200"

    return _timed("minio", probe, timeout=timeout)


CHECKS: Mapping[ComponentName, Callable[..., ComponentStatus]] = {
    "postgres": check_postgres,
    "mongo": check_mongo,
    "minio": check_minio,
}


def check_components(
    names: tuple[ComponentName, ...] = COMPONENT_NAMES,
    environ: Mapping[str, str] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> HealthReport:
    """Виконати перевірки названих компонентів і зібрати звіт; `ready` = усі `ok`."""
    components = tuple(CHECKS[name](environ, timeout=timeout) for name in names)
    return HealthReport(
        ready=all(component.ok for component in components),
        components=components,
        version=version_info(),
    )


def create_app() -> FastAPI:
    """FastAPI-застосунок лише з `GET /api/v1/health/components` (стаб; owner WP-11A)."""
    app = FastAPI(
        title="UA Web Data Collector — operator API (стаб WP-00)",
        version=version_info().package_version,
        # OpenAPI/docs вимикає стаб: реальний контракт §9.10 публікує WP-11A.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    @app.get(HEALTH_PATH, response_model=HealthReport)
    def health_components(response: Response) -> HealthReport:
        report = check_components()
        # 503, поки не готові всі критичні залежності: Docker healthcheck `api` і
        # `up --wait` вважають контейнер healthy лише за 200 (§7.5 readiness).
        response.status_code = 200 if report.ready else 503
        return report

    return app


def main(argv: list[str] | None = None) -> int:
    """Healthcheck-режим: `python -m collector.api.health [postgres] [mongo] [minio]`."""
    args = sys.argv[1:] if argv is None else argv
    names: tuple[ComponentName, ...] = COMPONENT_NAMES
    if args:
        unknown = [arg for arg in args if arg not in COMPONENT_NAMES]
        if unknown:
            print(f"unknown components: {', '.join(unknown)}", file=sys.stderr)
            return 2
        names = tuple(arg for arg in COMPONENT_NAMES if arg in args)
    report = check_components(names)
    for component in report.components:
        print(f"{component.name}: {'ok' if component.ok else 'error'} ({component.detail})")
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(main())
