"""Фабрика `AsyncMongoClient` з concerns §8/§9.5 (R-34) і опції транзакцій.

Один клієнт на процес. Concerns задаються на клієнті, а не на окремих викликах:
`readPreference=primary`, `readConcern=majority`, `writeConcern=majority`, `retryWrites=true`;
транзакції — `snapshot` read concern + `majority` write concern + primary. UUID зберігаються як
BSON Binary subtype 4 (`uuidRepresentation=standard`), datetime — BSON date UTC і читаються
tz-aware (`tz_aware=True`, `tzinfo=UTC`). Таймаути явні: server selection, socket і
`maxTimeMS` для операцій репозиторіїв (`MongoSettings.max_time_ms`).

URI компонента — env `COLLECTOR_MONGO_URI` або файл `COLLECTOR_MONGO_URI_FILE` (Docker secret
`mongo_uri_<component>`, WP-00 PR5); БД — із шляху URI або `COLLECTOR_MONGO_DATABASE`
(типово `collector`). URI ніколи не логується: `redacted_uri` лишає тільки хости.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from pymongo import AsyncMongoClient, ReadPreference
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.client_session_shared import TransactionOptions
from pymongo.read_concern import ReadConcern
from pymongo.uri_parser import parse_uri
from pymongo.write_concern import WriteConcern

from collector.core.config import env_or_file

MONGO_URI_ENV = "COLLECTOR_MONGO_URI"
MONGO_DATABASE_ENV = "COLLECTOR_MONGO_DATABASE"
DEFAULT_DATABASE = "collector"
DEFAULT_SERVER_SELECTION_TIMEOUT_MS = 10_000
DEFAULT_SOCKET_TIMEOUT_MS = 30_000
DEFAULT_MAX_TIME_MS = 15_000
DEFAULT_MAX_COMMIT_TIME_MS = 15_000

Document = dict[str, Any]


class MongoConfigError(ValueError):
    """URI відсутній або невалідний; повідомлення без URI."""


@dataclass(frozen=True, slots=True)
class MongoSettings:
    """Підключення компонента до domain-БД; `repr` не показує URI (там пароль)."""

    uri: str
    database: str = DEFAULT_DATABASE
    server_selection_timeout_ms: int = DEFAULT_SERVER_SELECTION_TIMEOUT_MS
    socket_timeout_ms: int = DEFAULT_SOCKET_TIMEOUT_MS
    max_time_ms: int = DEFAULT_MAX_TIME_MS
    max_commit_time_ms: int = DEFAULT_MAX_COMMIT_TIME_MS

    def __repr__(self) -> str:
        return f"MongoSettings(uri={self.redacted_uri!r}, database={self.database!r})"

    @property
    def redacted_uri(self) -> str:
        try:
            nodes = parse_uri(self.uri, validate=False, warn=False)["nodelist"]
        except Exception:  # будь-яка помилка розбору: URI у лог не потрапляє
            return "mongodb://<invalid>"
        return "mongodb://" + ",".join(f"{host}:{port}" for host, port in nodes)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MongoSettings:
        env: Mapping[str, str] = os.environ if environ is None else environ
        uri = env_or_file(MONGO_URI_ENV, env)
        if not uri:
            msg = f"задайте {MONGO_URI_ENV} або {MONGO_URI_ENV}_FILE"
            raise MongoConfigError(msg)
        try:
            parsed = parse_uri(uri, validate=True, warn=False)
        except Exception:  # текст помилки драйвера може містити частини URI (пароль)
            msg = f"{MONGO_URI_ENV}: невалідний URI"
            raise MongoConfigError(msg) from None
        database = parsed.get("database") or env.get(MONGO_DATABASE_ENV) or DEFAULT_DATABASE
        return cls(uri=uri, database=str(database))


def create_client(settings: MongoSettings, **overrides: Any) -> AsyncMongoClient[Document]:
    """`AsyncMongoClient` з concerns §8; `overrides` — для тестів (напр. `directConnection`)."""
    options: dict[str, Any] = {
        "readPreference": "primary",
        "readConcernLevel": "majority",
        "w": "majority",
        "retryWrites": True,
        "retryReads": True,
        "uuidRepresentation": "standard",
        "tz_aware": True,
        "tzinfo": UTC,
        "serverSelectionTimeoutMS": settings.server_selection_timeout_ms,
        "socketTimeoutMS": settings.socket_timeout_ms,
        "appname": "collector",
    }
    options.update(overrides)
    return AsyncMongoClient(settings.uri, **options)


def get_database(
    client: AsyncMongoClient[Document], settings: MongoSettings
) -> AsyncDatabase[Document]:
    return client.get_database(settings.database)


def transaction_options(settings: MongoSettings) -> TransactionOptions:
    """Опції однієї projection-транзакції (§8, R-34): snapshot/majority/primary, bounded commit."""
    return TransactionOptions(
        read_concern=ReadConcern("snapshot"),
        write_concern=WriteConcern("majority"),
        read_preference=ReadPreference.PRIMARY,
        max_commit_time_ms=settings.max_commit_time_ms,
    )
