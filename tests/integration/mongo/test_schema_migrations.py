"""WP-01B PR1: collections, validators (warn → error), indexes маніфесту, drift міграцій.

Усе з порожньої БД (`collector_test_<uuid>`) проти справжнього MongoDB 8.0 replica set.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from mongo_factories import current_document, receipt_document
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, WriteError

from collector.persistence.mongo import migrations
from collector.persistence.mongo.admin import IndexConflictError, apply_mongo_schema
from collector.persistence.mongo.migrations import (
    InvalidDocumentsError,
    MigrationDriftError,
    collection_options,
)
from collector.persistence.mongo.schema import (
    APPLIED_PROJECTION_RECEIPTS,
    CATALOG_ITEMS_CURRENT,
    CONTACT_OBSERVATIONS,
    CURRENT_COLLECTIONS,
    DOMAIN_COLLECTIONS,
    ENTITY_PROJECTION_VERSIONS,
    MIGRATIONS_COLLECTION,
    PRODUCT_REVIEWS,
    VEHICLE_OBSERVATIONS,
    ensure_indexes,
    index_keys,
    manifest_by_collection,
)

pytestmark = pytest.mark.integration

Db = Database[dict[str, Any]]
REPO_MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations" / "mongo"
VALIDATED = (*CURRENT_COLLECTIONS, APPLIED_PROJECTION_RECEIPTS)
DOCUMENT_VALIDATION_FAILURE = 121


def _state(db: Db) -> dict[str, Any]:
    """Порівнюваний знімок схеми: options collections, indexes, записи міграцій (без часу)."""
    collections = {
        info["name"]: {"type": info["type"], "options": info.get("options", {})}
        for info in db.list_collections()
    }
    indexes = {
        name: sorted(
            (index, index_keys(spec), bool(spec.get("unique", False)))
            for index, spec in db[name].index_information().items()
        )
        for name in collections
    }
    applied = sorted((doc["_id"], doc["checksum"]) for doc in db[MIGRATIONS_COLLECTION].find({}))
    return {"collections": collections, "indexes": indexes, "applied": applied}


def _full(root: MongoClient[dict[str, Any]], db: Db) -> None:
    apply_mongo_schema(root, db.name, validators=True, indexes=True)


def test_ensure_twice_gives_identical_state(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    first = apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True)
    state = _state(mongo_db)
    second = apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True)
    assert first.migrations_applied == [
        "0001_collections_validators_warn",
        "0002_validators_error",
    ]
    assert second.migrations_applied == []
    assert second.indexes is not None and second.indexes.created == []
    assert _state(mongo_db) == state


def test_all_collections_are_standard_and_indexes_match_manifest(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    _full(mongo_root, mongo_db)
    infos = {info["name"]: info for info in mongo_db.list_collections()}
    assert set(DOMAIN_COLLECTIONS) <= set(infos)
    assert len(DOMAIN_COLLECTIONS) == 11
    for name in DOMAIN_COLLECTIONS:
        assert infos[name]["type"] == "collection", name  # не time-series/view
        assert "timeseries" not in infos[name].get("options", {}), name
    manifest = manifest_by_collection()
    for name in DOMAIN_COLLECTIONS:
        actual = mongo_db[name].index_information()
        expected = manifest.get(name, {})
        assert set(actual) == {"_id_", *expected}, name
        for index, spec in expected.items():
            assert spec.matches(actual[index]), f"{name}.{index}"
        for info in actual.values():
            assert not any("$**" in key for key, _ in index_keys(info)), "wildcard index"


def test_validators_only_for_collections_with_snapshots(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    _full(mongo_root, mongo_db)
    for name in DOMAIN_COLLECTIONS:
        options = collection_options(mongo_db, name) or {}
        if name in VALIDATED:
            assert options["validationAction"] == "error", name
            assert options["validationLevel"] == "strict", name
            assert "$jsonSchema" in options["validator"], name
        else:
            assert "validator" not in options, name  # PR2 (рішення оркестратора п.8)


def test_valid_documents_pass_error_validators(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    """Документи з контрактів WP-01C (BSON mapping) проходять validators у режимі error."""
    _full(mongo_root, mongo_db)
    for name in CURRENT_COLLECTIONS:
        mongo_db[name].insert_one(current_document(item=f"item-{name}", catalog_item_id=uuid4()))
    mongo_db[APPLIED_PROJECTION_RECEIPTS].insert_many(
        [
            receipt_document(),
            receipt_document(applied=False, changed=False, version=2),
            receipt_document(version=3),
        ]
    )
    assert mongo_db[APPLIED_PROJECTION_RECEIPTS].count_documents({}) == 3


def test_invalid_document_warn_accepts_error_rejects_and_switch_refuses(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    applied = migrations.upgrade(mongo_db, target="0001")
    assert applied == ["0001_collections_validators_warn"]
    bad = current_document(item="bad")
    bad["projection_version"] = "три"  # не int
    bad["_id"] = str(bad["_id"])  # UUID рядком, а не BSON Binary subtype 4
    mongo_db[CATALOG_ITEMS_CURRENT].insert_one(bad)  # warn → приймається

    with pytest.raises(InvalidDocumentsError, match=f"{CATALOG_ITEMS_CURRENT}=1"):
        migrations.upgrade(mongo_db)
    for name in VALIDATED:  # усі або жодна: жодна collection не перемкнулась
        assert (collection_options(mongo_db, name) or {})["validationAction"] == "warn"
    assert "0002" not in {d["_id"] for d in mongo_db[MIGRATIONS_COLLECTION].find({})}

    mongo_db[CATALOG_ITEMS_CURRENT].delete_one({"_id": bad["_id"]})
    assert migrations.upgrade(mongo_db) == ["0002_validators_error"]
    with pytest.raises(WriteError) as rejected:
        mongo_db[CATALOG_ITEMS_CURRENT].insert_one(bad)
    assert rejected.value.code == DOCUMENT_VALIDATION_FAILURE
    missing = receipt_document()
    del missing["cluster_time"]
    with pytest.raises(WriteError):
        mongo_db[APPLIED_PROJECTION_RECEIPTS].insert_one(missing)
    extra = receipt_document(task_id=uuid4())
    extra["unexpected"] = 1  # receipt закритий: additionalProperties=false
    with pytest.raises(WriteError):
        mongo_db[APPLIED_PROJECTION_RECEIPTS].insert_one(extra)


def test_duplicate_natural_key_and_task_id_raise_duplicate_key(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    _full(mongo_root, mongo_db)
    items = mongo_db[CATALOG_ITEMS_CURRENT]
    items.insert_one(current_document(item="dup"))
    with pytest.raises(DuplicateKeyError):
        items.insert_one(current_document(item="dup"))  # інший _id, той самий source item

    task = uuid4()
    receipts = mongo_db[APPLIED_PROJECTION_RECEIPTS]
    receipts.insert_one(receipt_document(task_id=task))
    with pytest.raises(DuplicateKeyError):
        receipts.insert_one(receipt_document(task_id=task))  # PK `_id`
    other_pk = receipt_document(task_id=task)
    other_pk["_id"] = uuid4()
    with pytest.raises(DuplicateKeyError, match="ux_projection_task"):
        receipts.insert_one(other_pk)  # окремий unique index `projection_task_id`

    for name in (ENTITY_PROJECTION_VERSIONS, VEHICLE_OBSERVATIONS, CONTACT_OBSERVATIONS):
        mongo_db[name].insert_one({"projection_task_id": task, "entity_uuid": uuid4()})
        with pytest.raises(DuplicateKeyError):
            mongo_db[name].insert_one({"projection_task_id": task, "entity_uuid": uuid4()})

    entity = uuid4()
    versions = mongo_db[ENTITY_PROJECTION_VERSIONS]
    versions.insert_one({"entity_uuid": entity, "projection_version": 1, "projection_task_id": 1})
    with pytest.raises(DuplicateKeyError):
        versions.insert_one(
            {"entity_uuid": entity, "projection_version": 1, "projection_task_id": 2}
        )

    review = {"source": {"source_id": "s", "source_item_id": "r1"}, "content_version": "v1"}
    mongo_db[PRODUCT_REVIEWS].insert_one(dict(review))
    with pytest.raises(DuplicateKeyError):
        mongo_db[PRODUCT_REVIEWS].insert_one(dict(review))


def test_extra_index_is_reported_not_dropped(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    _full(mongo_root, mongo_db)
    mongo_db[CATALOG_ITEMS_CURRENT].create_index([("core.brand", 1)], name="ix_adhoc_brand")
    report = ensure_indexes(mongo_db)
    assert report.created == []
    assert report.conflicts == []
    assert [e.split(" ")[0] for e in report.extra] == [f"{CATALOG_ITEMS_CURRENT}.ix_adhoc_brand"]
    assert "ops=" in report.extra[0]
    assert "ix_adhoc_brand" in mongo_db[CATALOG_ITEMS_CURRENT].index_information()


def test_conflicting_index_fails_ensure(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    mongo_db[APPLIED_PROJECTION_RECEIPTS].create_index(
        [("projection_task_id", 1)], name="ux_projection_task"
    )  # не unique
    with pytest.raises(IndexConflictError, match="ux_projection_task"):
        apply_mongo_schema(mongo_root, mongo_db.name, validators=False, indexes=True)


def test_modified_or_missing_applied_migration_is_drift(mongo_db: Db, tmp_path: Path) -> None:
    copy = tmp_path / "mongo"
    shutil.copytree(REPO_MIGRATIONS, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert len(migrations.upgrade(mongo_db, migrations.discover(copy))) == 2
    assert migrations.upgrade(mongo_db, migrations.discover(copy)) == []

    module = next(copy.glob("0002_*.py"))
    module.write_text(module.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8")
    with pytest.raises(MigrationDriftError, match="0002_validators_error"):
        migrations.upgrade(mongo_db, migrations.discover(copy))

    shutil.copy(REPO_MIGRATIONS / module.name, module)
    asset = next((copy / "0001_collections_validators_warn").glob("*.json"))
    asset.write_text(asset.read_text(encoding="utf-8").replace('"string"', '"int"', 1), "utf-8")
    with pytest.raises(MigrationDriftError, match="0001_collections_validators_warn"):
        migrations.upgrade(mongo_db, migrations.discover(copy))

    shutil.rmtree(copy / "0001_collections_validators_warn")
    (copy / "0001_collections_validators_warn.py").unlink()
    with pytest.raises(MigrationDriftError, match="файлу немає"):
        migrations.upgrade(mongo_db, migrations.discover(copy))


def test_rerun_of_interrupted_migration_is_idempotent(mongo_db: Db) -> None:
    """Збій між `upgrade(db)` і записом у `schema_migrations` → повторний запуск доводить стан."""
    first = migrations.discover(REPO_MIGRATIONS)[0]
    first.load().upgrade(mongo_db)  # «впало» до запису про застосування
    assert mongo_db[MIGRATIONS_COLLECTION].count_documents({}) == 0
    assert migrations.upgrade(mongo_db, target="0001") == ["0001_collections_validators_warn"]
    assert (collection_options(mongo_db, CATALOG_ITEMS_CURRENT) or {})["validationAction"] == "warn"


def test_indexes_before_validators_still_converge(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    """`--indexes` на порожній БД створює collections неявно; `--validators` додає validator."""
    apply_mongo_schema(mongo_root, mongo_db.name, validators=False, indexes=True)
    assert "validator" not in (collection_options(mongo_db, CATALOG_ITEMS_CURRENT) or {})
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=False)
    options = collection_options(mongo_db, CATALOG_ITEMS_CURRENT) or {}
    assert options["validationAction"] == "error"
