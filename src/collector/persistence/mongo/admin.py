"""Тіло `collector db ensure-mongo --validators --indexes --users` після ініціалізації RS.

Порядок: міграції (collections + validators) → indexes маніфесту → custom roles і користувачі.
Кожен крок ідемпотентний, тож повторний запуск one-shot `ensure-mongo` дає той самий стан.
Помилки (`MigrationDriftError`, `InvalidDocumentsError`, `MongoUserError`, `IndexConflictError`,
`PyMongoError`) CLI перекладає в exit 1; повідомлення не містять URI/паролів.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pymongo import MongoClient, WriteConcern

from collector.persistence.mongo import migrations
from collector.persistence.mongo.schema import IndexReport, ensure_indexes
from collector.persistence.mongo.users import MongoUserCredential, apply_users


class IndexConflictError(RuntimeError):
    """Index маніфесту конфліктує з наявним (ім'я/ключі/unique)."""


@dataclass(slots=True)
class SchemaResult:
    migrations_applied: list[str] = field(default_factory=list)
    indexes: IndexReport | None = None
    users: list[str] = field(default_factory=list)


def apply_mongo_schema(
    client: MongoClient[dict[str, Any]],
    database: str,
    *,
    validators: bool,
    indexes: bool,
    credentials: Sequence[MongoUserCredential] | None = None,
) -> SchemaResult:
    """Застосувати вибрані кроки до БД `database` під root-клієнтом `ensure-mongo`."""
    db = client.get_database(database, write_concern=WriteConcern("majority"))
    result = SchemaResult()
    if validators:
        result.migrations_applied = migrations.upgrade(db)
    if indexes:
        report = ensure_indexes(db)
        result.indexes = report
        if report.conflicts:
            raise IndexConflictError("; ".join(report.conflicts))
    if credentials is not None:
        result.users = apply_users(client, database, credentials)
    return result
