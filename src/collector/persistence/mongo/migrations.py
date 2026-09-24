"""Forward-only Mongo-міграції `migrations/mongo/NNNN_<name>.py` (`db ensure-mongo --validators`).

Контракт модуля міграції: функція `upgrade(db: pymongo.database.Database) -> None`, яка
**ідемпотентна** (повторний виклик після часткового збою доводить стан до кінця). Поруч може
лежати каталог `NNNN_<name>/` з asset-ами (заморожені `$jsonSchema`, `validators.RECIPES`).

Службова collection `schema_migrations` зберігає `{_id: "NNNN", name, checksum, applied_at}`.
Checksum — SHA-256 модуля і всіх asset-ів (CRLF → LF, щоб Windows-checkout і Linux image давали
той самий результат). Повторний запуск — no-op; змінений, зниклий або невідомий застосований
модуль — `MigrationDriftError` **до** будь-якого запису: відкат лише новою forward-міграцією
(напр. послаблення `validationAction`), downgrade не підтримується.

Цикл validators §9.2: міграція ставить `validationAction: warn` (`ensure_collection`) → окрема
наступна міграція перемикає в `error` через `require_valid_then_error`, яка відмовляє, якщо в
collection є документи, що не проходять validator (`{$nor: [validator]}`).

Пошук каталогу: env `COLLECTOR_MONGO_MIGRATIONS_DIR` → `migrations/mongo` вгору від cwd → вгору
від файлу пакета (Docker image копіює `migrations/` у `/app/migrations`, WORKDIR `/app`).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from pymongo.database import Database

from collector.persistence.mongo.schema import MIGRATIONS_COLLECTION

MIGRATIONS_DIR_ENV = "COLLECTOR_MONGO_MIGRATIONS_DIR"
_MODULE_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.py$")
ValidationAction = Literal["warn", "error"]
Document = dict[str, Any]


class MigrationsNotFoundError(FileNotFoundError):
    """Каталог `migrations/mongo` не знайдено."""


class MigrationDriftError(RuntimeError):
    """Застосовані міграції не збігаються з файлами (змінений/зниклий/невідомий модуль)."""


class InvalidDocumentsError(RuntimeError):
    """Перемикання validator у `error` неможливе: у collection є невалідні документи."""


@dataclass(frozen=True, slots=True)
class MongoMigration:
    """Файл міграції; `checksum` рахується з модуля та asset-ів під час `discover`."""

    version: str
    name: str
    path: Path
    checksum: str

    @property
    def asset_dir(self) -> Path:
        return self.path.with_suffix("")

    def load(self) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            f"collector_mongo_migration_{self.version}", self.path
        )
        if spec is None or spec.loader is None:
            msg = f"не вдалося завантажити міграцію {self.path.name}"
            raise MigrationsNotFoundError(msg)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "upgrade", None)):
            msg = f"{self.path.name}: немає функції upgrade(db)"
            raise MigrationDriftError(msg)
        return module


def find_migrations_dir(start: Path | None = None) -> Path:
    """`COLLECTOR_MONGO_MIGRATIONS_DIR` або перший `migrations/mongo` вгору від cwd/пакета."""
    override = os.environ.get(MIGRATIONS_DIR_ENV)
    if override:
        path = Path(override)
        if path.is_dir():
            return path
        msg = f"{MIGRATIONS_DIR_ENV}={override!r}: каталог не знайдено"
        raise MigrationsNotFoundError(msg)
    for root in (start or Path.cwd(), Path(__file__).resolve()):
        for candidate in (root, *root.parents):
            path = candidate / "migrations" / "mongo"
            if path.is_dir():
                return path
    msg = f"migrations/mongo не знайдено (задайте {MIGRATIONS_DIR_ENV})"
    raise MigrationsNotFoundError(msg)


def _normalized(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def migration_checksum(path: Path) -> str:
    """SHA-256 модуля і asset-ів `NNNN_<name>/**` (відносні шляхи + вміст, LF)."""
    digest = hashlib.sha256()
    digest.update(_normalized(path))
    asset_dir = path.with_suffix("")
    if asset_dir.is_dir():
        for asset in sorted(p for p in asset_dir.rglob("*") if p.is_file()):
            if "__pycache__" in asset.parts:
                continue
            digest.update(b"\0" + asset.relative_to(asset_dir).as_posix().encode() + b"\0")
            digest.update(_normalized(asset))
    return digest.hexdigest()


def discover(directory: Path | None = None) -> list[MongoMigration]:
    """Усі `NNNN_<name>.py`, відсортовані за версією; дубль версії — помилка."""
    root = directory or find_migrations_dir()
    found: dict[str, MongoMigration] = {}
    for path in sorted(root.glob("*.py")):
        match = _MODULE_RE.match(path.name)
        if match is None:
            continue
        version = match["version"]
        if version in found:
            msg = f"дві міграції з версією {version}: {found[version].path.name}, {path.name}"
            raise MigrationDriftError(msg)
        found[version] = MongoMigration(version, match["name"], path, migration_checksum(path))
    return [found[v] for v in sorted(found)]


def applied_migrations(db: Database[Document]) -> dict[str, Document]:
    return {str(doc["_id"]): doc for doc in db[MIGRATIONS_COLLECTION].find({})}


def check_drift(db: Database[Document], migrations: Sequence[MongoMigration]) -> list[str]:
    """Розбіжності застосованих записів із файлами; порожній список — drift немає."""
    by_version = {m.version: m for m in migrations}
    problems: list[str] = []
    for version, doc in sorted(applied_migrations(db).items()):
        migration = by_version.get(version)
        if migration is None:
            problems.append(f"{version}_{doc.get('name')}: застосована, але файлу немає")
        elif doc.get("checksum") != migration.checksum:
            problems.append(
                f"{version}_{migration.name}: модуль змінено після застосування "
                "(checksum не збігається) — потрібна нова forward-міграція"
            )
    return problems


def upgrade(
    db: Database[Document],
    migrations: Sequence[MongoMigration] | None = None,
    *,
    target: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[str]:
    """Застосовує незастосовані міграції до `target` включно (типово — усі). Повертає імена.

    Drift перевіряється до першого запису. Запис у `schema_migrations` робиться після
    успішного `upgrade(db)`; збій посередині лишає міграцію незастосованою, і повторний запуск
    виконує її знову (саме тому `upgrade` мусить бути ідемпотентним).
    """
    items = list(discover() if migrations is None else migrations)
    problems = check_drift(db, items)
    if problems:
        raise MigrationDriftError("; ".join(problems))
    done = applied_migrations(db)
    applied: list[str] = []
    for migration in items:
        if target is not None and migration.version > target:
            break
        if migration.version in done:
            continue
        migration.load().upgrade(db)
        db[MIGRATIONS_COLLECTION].insert_one(
            {
                "_id": migration.version,
                "name": migration.name,
                "checksum": migration.checksum,
                "applied_at": now(),
            }
        )
        applied.append(f"{migration.version}_{migration.name}")
    return applied


# --- helpers для модулів міграцій ------------------------------------------------------------


def load_asset(module_file: str, name: str) -> Document:
    """Asset `<NNNN_name>/<name>.json` поруч із модулем міграції (`__file__` модуля)."""
    path = Path(module_file).with_suffix("") / f"{name}.json"
    loaded: Document = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def collection_options(db: Database[Document], name: str) -> Document | None:
    """`options` collection з `listCollections` або None, якщо collection не існує."""
    for info in db.list_collections(filter={"name": name}):
        options: Document = dict(info.get("options", {}))
        return options
    return None


def ensure_collection(
    db: Database[Document],
    name: str,
    *,
    validator: Mapping[str, Any] | None = None,
    action: ValidationAction = "warn",
) -> None:
    """Стандартна collection; з `validator` — `validationLevel: strict` і заданий action.

    Наявна collection (напр. неявно створена `--indexes`) отримує validator через `collMod`.
    """
    options = collection_options(db, name)
    settings: Document = {}
    if validator is not None:
        settings = {
            "validator": dict(validator),
            "validationLevel": "strict",
            "validationAction": action,
        }
    if options is None:
        db.create_collection(name, **settings)
    elif settings:
        db.command("collMod", name, **settings)


def invalid_document_count(db: Database[Document], name: str) -> int:
    """Кількість документів, що не проходять поточний validator collection (0 без validator)."""
    options = collection_options(db, name) or {}
    validator = options.get("validator")
    if not validator:
        return 0
    return db[name].count_documents({"$nor": [validator]})


def require_valid_then_error(db: Database[Document], names: Iterable[str]) -> None:
    """`warn → error` для всіх `names` або жодної: спершу перевірка всіх, потім `collMod`."""
    targets = list(names)
    invalid = {name: invalid_document_count(db, name) for name in targets}
    bad = {name: count for name, count in invalid.items() if count}
    if bad:
        detail = ", ".join(f"{name}={count}" for name, count in sorted(bad.items()))
        msg = f"validationAction=error відхилено: невалідні документи ({detail})"
        raise InvalidDocumentsError(msg)
    for name in targets:
        options = collection_options(db, name) or {}
        if not options.get("validator"):
            msg = f"{name}: validator відсутній, перемикати в error нічого"
            raise InvalidDocumentsError(msg)
        db.command("collMod", name, validationLevel="strict", validationAction="error")
