"""WP-01B PR1 — adversarial-тести незалежного тестувальника (§16.1 п.3, картка WP-01B PR1).

Покриває те, чого немає в базових тестах реалізатора: кожен unique index маніфесту §9.2
(включно з конкурентною вставкою), попередження validator-а у warn-режимі, ідемпотентність
`warn → error` і повторного `ensure-mongo --validators --indexes --users` без втрати даних/
перебудови indexes, конфлікт визначення index з тим самим ім'ям, мінімальні права
Mongo-користувачів (§13), відсутність паролів у виводі/винятках, обов'язкові поля відкритого
кореня validator-а current collections.
"""

from __future__ import annotations

import secrets
import threading
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from uuid import uuid4

import pytest
from mongo_factories import current_document, receipt_document
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, OperationFailure, WriteError
from typer.testing import CliRunner

from collector.cli import app
from collector.persistence.mongo import migrations
from collector.persistence.mongo.admin import IndexConflictError, apply_mongo_schema
from collector.persistence.mongo.migrations import (
    MIGRATIONS_DIR_ENV,
    collection_options,
    require_valid_then_error,
)
from collector.persistence.mongo.schema import (
    APPLIED_PROJECTION_RECEIPTS,
    CATALOG_ITEMS_CURRENT,
    CURRENT_COLLECTIONS,
    DOMAIN_COLLECTIONS,
    ENTITY_PROJECTION_VERSIONS,
    INDEX_MANIFEST,
    MIGRATIONS_COLLECTION,
    SELLERS_CURRENT,
    IndexSpec,
)
from collector.persistence.mongo.users import (
    USER_COMPONENTS,
    MongoUserCredential,
    role_privileges,
    user_name,
)

pytestmark = pytest.mark.integration

Db = Database[dict[str, Any]]
UNAUTHORIZED = 13
DOCUMENT_VALIDATION_FAILURE = 121
VALIDATED = (*CURRENT_COLLECTIONS, APPLIED_PROJECTION_RECEIPTS)
UNIQUE_SPECS = [spec for spec in INDEX_MANIFEST if spec.unique]
runner = CliRunner()


def _doc_for(spec: IndexSpec, marker: str) -> dict[str, Any]:
    """Мінімальний документ, що несе ключі `spec` з однаковими значеннями (для дубля)."""
    doc: dict[str, Any] = {"marker": marker}

    def set_path(key: str, value: str) -> None:
        head, _, tail = key.partition(".")
        if tail:
            doc.setdefault(head, {})[tail] = value
        else:
            doc[head] = value

    # Mongo unique indexes also index missing fields as null. Fill every other unique key on
    # the same collection with marker-specific values so this test isolates exactly `spec`.
    for other in UNIQUE_SPECS:
        if other.collection == spec.collection:
            for key, _direction in other.keys:
                set_path(key, f"{marker}-{key}")
    for key, _direction in spec.keys:
        set_path(key, f"dup-{key.rsplit('.', 1)[-1]}")
    return doc


# --- 1. кожен unique index §9.2 відхиляє дубль, у т.ч. конкурентно ---------------------------


def test_manifest_has_all_unique_indexes_of_spec_9_2() -> None:
    """Рядки §9.2 з unique: current source item ×4, EPV ×2, observations ×3, reviews ×2, receipt."""
    unique = {(s.collection, s.keys) for s in UNIQUE_SPECS}
    task = (("projection_task_id", 1),)
    source = (("source.source_id", 1), ("source.source_item_id", 1))
    for name in CURRENT_COLLECTIONS:
        assert (name, source) in unique, name
    assert (ENTITY_PROJECTION_VERSIONS, (("entity_uuid", 1), ("projection_version", 1))) in unique
    for name in (
        ENTITY_PROJECTION_VERSIONS,
        "catalog_offer_observations",
        "vehicle_observations",
        "contact_observations",
        APPLIED_PROJECTION_RECEIPTS,
    ):
        assert (name, task) in unique, name
    for name in ("product_reviews", "product_questions"):
        assert (name, (*source, ("content_version", 1))) in unique, name
    assert len(UNIQUE_SPECS) == 12


@pytest.mark.parametrize("spec", UNIQUE_SPECS, ids=lambda s: f"{s.collection}.{s.name}")
def test_every_unique_index_rejects_duplicate(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db, spec: IndexSpec
) -> None:
    apply_mongo_schema(mongo_root, mongo_db.name, validators=False, indexes=True)
    coll = mongo_db[spec.collection]
    coll.insert_one(_doc_for(spec, "first"))
    with pytest.raises(DuplicateKeyError, match=spec.name):
        coll.insert_one(_doc_for(spec, "second"))
    # update, що створює дубль, теж відхиляється
    other = _doc_for(spec, "third")
    first_key = spec.keys[0][0]
    head, _, tail = first_key.partition(".")
    if tail:
        other[head][tail] = "different"
    else:
        other[head] = "different"
    coll.insert_one(other)
    with pytest.raises(DuplicateKeyError):
        coll.update_one({"marker": "third"}, {"$set": {first_key: f"dup-{tail or head}"}})
    assert coll.count_documents({}) == 2


@pytest.mark.parametrize("spec", UNIQUE_SPECS, ids=lambda s: f"{s.collection}.{s.name}")
def test_every_unique_index_under_concurrent_inserts(
    mongo_server: Any, mongo_root: MongoClient[dict[str, Any]], mongo_db: Db, spec: IndexSpec
) -> None:
    """N потоків (окремі клієнти) одночасно вставляють той самий ключ → рівно один успіх."""
    apply_mongo_schema(mongo_root, mongo_db.name, validators=False, indexes=True)
    workers = 6
    barrier = threading.Barrier(workers)
    outcomes: list[str] = []
    lock = threading.Lock()
    clients = [mongo_server.sync_client() for _ in range(workers)]

    def insert(index: int) -> None:
        coll = clients[index].get_database(mongo_db.name)[spec.collection]
        barrier.wait(timeout=30)
        try:
            coll.insert_one(_doc_for(spec, f"w{index}"))
            result = "ok"
        except DuplicateKeyError:
            result = "dup"
        except Exception as exc:  # будь-що інше — провал тесту нижче
            result = f"error:{type(exc).__name__}"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=insert, args=(i,)) for i in range(workers)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        for client in clients:
            client.close()
    assert sorted(outcomes) == ["dup"] * (workers - 1) + ["ok"], outcomes
    assert mongo_db[spec.collection].count_documents({}) == 1


def test_concurrent_receipt_transactions_same_task_one_wins(
    mongo_server: Any, mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    """Дві транзакції з тим самим `projection_task_id` (модель подвійного виконання) → 1 receipt."""
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True)
    task = uuid4()
    workers = 4
    barrier = threading.Barrier(workers)
    outcomes: list[str] = []
    lock = threading.Lock()
    clients = [mongo_server.sync_client() for _ in range(workers)]

    def run(index: int) -> None:
        client = clients[index]
        coll = client.get_database(mongo_db.name)[APPLIED_PROJECTION_RECEIPTS]
        doc = receipt_document(task_id=task)
        result = "?"
        try:
            with client.start_session() as session, session.start_transaction():
                barrier.wait(timeout=30)
                coll.insert_one(doc, session=session)
            result = "ok"
        except (DuplicateKeyError, OperationFailure) as exc:
            if isinstance(exc, DuplicateKeyError) or exc.has_error_label(
                "TransientTransactionError"
            ):
                result = "rejected"
            else:
                result = f"error:{exc.code}"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        for client in clients:
            client.close()
    assert outcomes.count("ok") == 1, outcomes
    assert outcomes.count("rejected") == workers - 1, outcomes
    assert mongo_db[APPLIED_PROJECTION_RECEIPTS].count_documents({"projection_task_id": task}) == 1


# --- 2. validator warn → error ----------------------------------------------------------------


def _validation_warnings(root: MongoClient[dict[str, Any]], namespace: str) -> list[str]:
    lines = root.admin.command({"getLog": "global"})["log"]
    return [line for line in lines if "would fail validation" in line and namespace in line]


def test_warn_mode_writes_invalid_document_and_logs_warning(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    migrations.upgrade(mongo_db, target="0001")
    for name in VALIDATED:
        assert (collection_options(mongo_db, name) or {})["validationAction"] == "warn"
    bad = current_document(item=f"warn-{uuid4().hex[:6]}")
    del bad["lineage"]  # обов'язкове поле
    namespace = f"{mongo_db.name}.{SELLERS_CURRENT}"
    before = len(_validation_warnings(mongo_root, namespace))
    mongo_db[SELLERS_CURRENT].insert_one(bad)
    assert mongo_db[SELLERS_CURRENT].count_documents({"_id": bad["_id"]}) == 1
    assert len(_validation_warnings(mongo_root, namespace)) == before + 1


def test_error_mode_rejects_and_warn_to_error_is_idempotent(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True)
    snapshot = {name: collection_options(mongo_db, name) for name in VALIDATED}
    # повторне перемикання: і helper, і модуль 0002, і повний upgrade — без змін і без помилок
    require_valid_then_error(mongo_db, VALIDATED)
    migrations.discover()[1].load().upgrade(mongo_db)
    assert migrations.upgrade(mongo_db) == []
    assert {name: collection_options(mongo_db, name) for name in VALIDATED} == snapshot
    bad = receipt_document()
    del bad["result_hash"]
    with pytest.raises(WriteError) as info:
        mongo_db[APPLIED_PROJECTION_RECEIPTS].insert_one(bad)
    assert info.value.code == DOCUMENT_VALIDATION_FAILURE
    assert mongo_db[APPLIED_PROJECTION_RECEIPTS].count_documents({}) == 0


def test_error_mode_rejects_update_that_breaks_schema(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    """`validationLevel: strict` — invalid update валідного документа теж відхиляється."""
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True)
    doc = current_document(item="upd")
    mongo_db[CATALOG_ITEMS_CURRENT].insert_one(doc)
    with pytest.raises(WriteError) as info:
        mongo_db[CATALOG_ITEMS_CURRENT].update_one(
            {"_id": doc["_id"]}, {"$unset": {"state_hash": 1}}
        )
    assert info.value.code == DOCUMENT_VALIDATION_FAILURE


# --- 3. відкритий корінь current validator: обов'язкові поля все одно валідуються -------------

CURRENT_REQUIRED_TOP = (
    "_id",
    "core",
    "entity_kind",
    "first_seen_at",
    "identity_hash",
    "last_seen_at",
    "lineage",
    "projection_version",
    "source",
    "state_hash",
    "time",
)


@pytest.fixture
def error_schema(mongo_root: MongoClient[dict[str, Any]], mongo_db: Db) -> Db:
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=False)
    return mongo_db


def _rejected(db: Db, collection: str, doc: dict[str, Any]) -> None:
    with pytest.raises(WriteError) as info:
        db[collection].insert_one(doc)
    assert info.value.code == DOCUMENT_VALIDATION_FAILURE


@pytest.mark.parametrize("field", CURRENT_REQUIRED_TOP)
@pytest.mark.parametrize("collection", CURRENT_COLLECTIONS)
def test_open_root_still_requires_top_level_fields(
    error_schema: Db, collection: str, field: str
) -> None:
    doc = current_document(item=f"req-{field}")
    del doc[field]
    if field == "_id":
        doc["_id"] = str(uuid4())  # _id завжди є; перевіряємо BSON-тип (binData subtype 4)
    _rejected(error_schema, collection, doc)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("source.source_id", None),
        ("source.source_item_id", None),
        ("lineage.projection_task_id", None),
        ("time.observed_at", None),
        ("projection_version", 0),
        ("projection_version", "1"),
        ("state_hash", "not-a-hash"),
        ("entity_kind", "news"),
        ("last_seen_at", "2026-09-01T12:00:00Z"),  # рядок, а не BSON date
        ("schema_version", "1"),
    ],
)
def test_open_root_still_validates_nested_and_types(
    error_schema: Db, path: str, value: Any
) -> None:
    doc = current_document(item=f"typ-{path}")
    head, _, tail = path.partition(".")
    if tail:
        if value is None:
            del doc[head][tail]
        else:
            doc[head][tail] = value
    else:
        doc[head] = value
    _rejected(error_schema, CATALOG_ITEMS_CURRENT, doc)


def test_open_root_accepts_domain_fields_but_nested_objects_are_closed(error_schema: Db) -> None:
    ok = current_document(item="open", catalog_item_id=uuid4(), domain_extra={"x": 1})
    error_schema[CATALOG_ITEMS_CURRENT].insert_one(ok)  # корінь відкритий для WP-07/WP-09
    closed = current_document(item="closed")
    closed["source"]["unexpected"] = "x"
    _rejected(error_schema, CATALOG_ITEMS_CURRENT, closed)


def test_current_requires_schema_version_per_spec_9_2(error_schema: Db) -> None:
    """§9.2: `schema_version` — обов'язкове поле будь-якого `*_current`."""
    doc = current_document(item="no-schema-version")
    del doc["schema_version"]
    _rejected(error_schema, CATALOG_ITEMS_CURRENT, doc)


# --- 4. повторний ensure-mongo --validators --indexes --users ---------------------------------


def _index_since(db: Db, collection: str) -> dict[str, Any]:
    return {
        s["name"]: s["accesses"]["since"] for s in db[collection].aggregate([{"$indexStats": {}}])
    }


@pytest.mark.usefixtures("mongo_users_cleanup")
def test_repeated_full_ensure_keeps_data_and_does_not_rebuild_indexes(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    creds = [MongoUserCredential(c, secrets.token_hex(12)) for c in USER_COMPONENTS]
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True, credentials=creds)
    doc = current_document(item="keep")
    mongo_db[CATALOG_ITEMS_CURRENT].insert_one(doc)
    mongo_db[APPLIED_PROJECTION_RECEIPTS].insert_one(receipt_document())
    mongo_db[CATALOG_ITEMS_CURRENT].create_index([("core.brand", 1)], name="ix_operator_extra")
    since = {name: _index_since(mongo_db, name) for name in DOMAIN_COLLECTIONS}
    applied = list(mongo_db[MIGRATIONS_COLLECTION].find({}))
    roles_before = mongo_root.admin.command(
        "rolesInfo", {"role": "collector_projector", "db": "admin"}, showPrivileges=True
    )["roles"]

    for _ in range(2):
        result = apply_mongo_schema(
            mongo_root, mongo_db.name, validators=True, indexes=True, credentials=creds
        )
        assert result.migrations_applied == []
        assert result.indexes is not None
        assert result.indexes.created == [] and result.indexes.conflicts == []
        assert [e.split(" ")[0] for e in result.indexes.extra] == [
            f"{CATALOG_ITEMS_CURRENT}.ix_operator_extra"
        ]

    assert mongo_db[CATALOG_ITEMS_CURRENT].find_one({"_id": doc["_id"]}) is not None
    assert mongo_db[APPLIED_PROJECTION_RECEIPTS].count_documents({}) == 1
    assert {name: _index_since(mongo_db, name) for name in DOMAIN_COLLECTIONS} == since
    assert list(mongo_db[MIGRATIONS_COLLECTION].find({})) == applied
    roles_after = mongo_root.admin.command(
        "rolesInfo", {"role": "collector_projector", "db": "admin"}, showPrivileges=True
    )["roles"]
    assert roles_after[0]["privileges"] == roles_before[0]["privileges"]


# --- 5. змінене визначення index з тим самим ім'ям → явна помилка ------------------------------


@pytest.mark.parametrize(
    ("collection", "name", "keys", "options"),
    [
        # ті самі ім'я, інші ключі
        (CATALOG_ITEMS_CURRENT, "ux_source_item", [("source.source_item_id", 1)], {"unique": True}),
        # той самий набір полів, інший порядок
        (
            CATALOG_ITEMS_CURRENT,
            "ux_source_item",
            [("source.source_item_id", 1), ("source.source_id", 1)],
            {"unique": True},
        ),
        # інший напрям
        (SELLERS_CURRENT, "ix_last_seen", [("last_seen_at", 1)], {}),
        # unique → не unique
        (ENTITY_PROJECTION_VERSIONS, "ux_projection_task", [("projection_task_id", 1)], {}),
        # не unique → unique
        (SELLERS_CURRENT, "ix_last_seen", [("last_seen_at", -1)], {"unique": True}),
    ],
)
def test_changed_index_definition_same_name_is_explicit_error(
    mongo_root: MongoClient[dict[str, Any]],
    mongo_db: Db,
    collection: str,
    name: str,
    keys: list[tuple[str, int]],
    options: dict[str, Any],
) -> None:
    mongo_db[collection].create_index(keys, name=name, **options)
    with pytest.raises(IndexConflictError, match=name):
        apply_mongo_schema(mongo_root, mongo_db.name, validators=False, indexes=True)
    # нічого не перебудовано мовчки
    info = mongo_db[collection].index_information()[name]
    assert [tuple(k) for k in info["key"]] == [tuple(k) for k in keys]


@pytest.mark.parametrize(
    "options",
    [
        {"unique": True, "sparse": True},
        {"unique": True, "partialFilterExpression": {"projection_task_id": {"$type": "string"}}},
        {"unique": True, "collation": {"locale": "uk", "strength": 1}},
    ],
    ids=["sparse", "partial", "collation"],
)
def test_same_name_keys_unique_but_other_options_is_explicit_error(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db, options: dict[str, Any]
) -> None:
    """Sparse/partial/collation змінюють семантику unique — це теж інше визначення index."""
    mongo_db[APPLIED_PROJECTION_RECEIPTS].create_index(
        [("projection_task_id", 1)], name="ux_projection_task", **options
    )
    with pytest.raises(IndexConflictError, match="ux_projection_task"):
        apply_mongo_schema(mongo_root, mongo_db.name, validators=False, indexes=True)


def test_same_keys_other_name_is_explicit_error(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    mongo_db[SELLERS_CURRENT].create_index([("last_seen_at", -1)], name="legacy_last_seen")
    with pytest.raises(IndexConflictError, match="legacy_last_seen"):
        apply_mongo_schema(mongo_root, mongo_db.name, validators=False, indexes=True)


def test_cli_index_conflict_exits_1(
    monkeypatch: pytest.MonkeyPatch, mongo_server: Any, mongo_db: Db
) -> None:
    _cli_env(monkeypatch, mongo_server, mongo_db)
    mongo_db[SELLERS_CURRENT].create_index([("last_seen_at", 1)], name="ix_last_seen")
    result = runner.invoke(app, ["db", "ensure-mongo", "--validators", "--indexes"])
    assert result.exit_code == 1
    assert "ix_last_seen" in result.stderr
    assert mongo_server.password not in result.output


# --- 6. Mongo-користувачі за компонентами (§13): лише потрібні ролі ---------------------------


@pytest.fixture
def users(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db, mongo_users_cleanup: None
) -> dict[str, MongoUserCredential]:
    creds = [MongoUserCredential(c, secrets.token_hex(12)) for c in USER_COMPONENTS]
    apply_mongo_schema(mongo_root, mongo_db.name, validators=True, indexes=True, credentials=creds)
    return {c.component: c for c in creds}


@pytest.fixture
def login(mongo_server: Any, users: dict[str, MongoUserCredential]) -> Iterator[Any]:
    clients: list[MongoClient[dict[str, Any]]] = []

    def _login(component: str) -> MongoClient[dict[str, Any]]:
        cred = users[component]
        client: MongoClient[dict[str, Any]] = MongoClient(
            mongo_server.uri(cred.user, cred.password),
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=10_000,
        )
        clients.append(client)
        return client

    yield _login
    for client in clients:
        client.close()


def _denied(action: Any) -> None:
    with pytest.raises(OperationFailure) as info:
        action()
    assert info.value.code == UNAUTHORIZED, info.value.details


@pytest.mark.parametrize("component", USER_COMPONENTS)
def test_component_cannot_drop_database_or_touch_foreign_namespaces(
    login: Any, mongo_db: Db, mongo_root: MongoClient[dict[str, Any]], component: str
) -> None:
    other_name = f"collector_test_other_{uuid4().hex[:8]}"
    mongo_root[other_name]["catalog_items_current"].insert_one({"x": 1})
    try:
        client = login(component)
        db = client.get_database(mongo_db.name)
        _denied(lambda: client.drop_database(mongo_db.name))
        _denied(lambda: db.command("dropDatabase"))
        _denied(lambda: db[MIGRATIONS_COLLECTION].insert_one({"_id": "9999"}))
        _denied(lambda: db[MIGRATIONS_COLLECTION].find_one({}))
        _denied(lambda: db["foreign_collection"].insert_one({"x": 1}))
        _denied(lambda: db.create_collection("created_by_runtime"))
        _denied(lambda: client[other_name]["catalog_items_current"].insert_one({"x": 2}))
        _denied(lambda: client[other_name]["catalog_items_current"].find_one({}))
        intruder_pw = secrets.token_hex(8)
        _denied(lambda: client.admin.command("createUser", "intruder", pwd=intruder_pw, roles=[]))
        _denied(
            lambda: client.admin.command("grantRolesToUser", user_name(component), roles=["root"])
        )
        _denied(lambda: client["admin"]["system.users"].find_one({}))
        for name in DOMAIN_COLLECTIONS:
            _denied(lambda n=name: db.drop_collection(n))
    finally:
        mongo_root.drop_database(other_name)


def test_role_privileges_are_exactly_the_minimum(
    users: dict[str, MongoUserCredential], mongo_root: MongoClient[dict[str, Any]], mongo_db: Db
) -> None:
    admin = mongo_root.admin
    expected_actions = {
        "collector_projector": {"find", "insert", "update"},
        "collector_compactor": {"find", "insert", "update"},
        "collector_api_ro": {"find"},
        "collector_export_ro": {"find"},
    }
    for component in USER_COMPONENTS:
        role = user_name(component)
        info = admin.command("rolesInfo", {"role": role, "db": "admin"}, showPrivileges=True)
        (role_doc,) = info["roles"]
        assert role_doc["roles"] == [], role  # без успадкованих ролей
        by_collection: dict[str, set[str]] = {}
        for priv in role_doc["privileges"]:
            resource = priv["resource"]
            assert resource.get("db") == mongo_db.name, (role, resource)
            by_collection[resource["collection"]] = set(priv["actions"])
        extra_ns = set(by_collection) - {*DOMAIN_COLLECTIONS, ""}
        assert not extra_ns, (role, extra_ns)
        for name in DOMAIN_COLLECTIONS:
            want = set(expected_actions[role])
            if role == "collector_compactor" and name == ENTITY_PROJECTION_VERSIONS:
                want.add("remove")
            assert by_collection[name] == want, (role, name, by_collection[name])
        if role in ("collector_projector", "collector_compactor"):
            assert by_collection[""] == {"listCollections"}, role
        else:
            assert "" not in by_collection, role
        user = admin.command("usersInfo", role)["users"][0]
        assert [(r["role"], r["db"]) for r in user["roles"]] == [(role, "admin")]
    assert role_privileges(mongo_db.name).keys() == {user_name(c) for c in USER_COMPONENTS}


def test_no_mongo_users_for_scheduler_fetcher_parser(
    users: dict[str, MongoUserCredential], mongo_root: MongoClient[dict[str, Any]]
) -> None:
    for component in ("scheduler", "fetcher", "parser", "fetch", "parse", "migrator"):
        name = f"collector_{component}"
        assert mongo_root.admin.command("usersInfo", name)["users"] == [], name


def test_api_ro_cannot_write_receipts_or_versions(login: Any, mongo_db: Db) -> None:
    db = login("api_ro").get_database(mongo_db.name)
    _denied(lambda: db[APPLIED_PROJECTION_RECEIPTS].insert_one(receipt_document()))
    _denied(lambda: db[ENTITY_PROJECTION_VERSIONS].insert_one({"projection_task_id": uuid4()}))
    _denied(lambda: db[ENTITY_PROJECTION_VERSIONS].delete_many({}))


# --- 7. паролі з секретів не потрапляють у вивід/винятки ---------------------------------------


def _cli_env(monkeypatch: pytest.MonkeyPatch, mongo_server: Any, mongo_db: Db) -> None:
    monkeypatch.setenv("COLLECTOR_MONGO_HOST", mongo_server.host)
    monkeypatch.setenv("COLLECTOR_MONGO_PORT", str(mongo_server.port))
    monkeypatch.setenv("COLLECTOR_MONGO_REPLICA_SET", "rs0")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_USERNAME", mongo_server.username)
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_PASSWORD", mongo_server.password)
    monkeypatch.delenv("COLLECTOR_MONGO_ROOT_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("COLLECTOR_MONGO_DATABASE", mongo_db.name)
    monkeypatch.delenv(MIGRATIONS_DIR_ENV, raising=False)


def _write_secrets(directory: Path, passwords: dict[str, str], broken: dict[str, str]) -> None:
    for component, password in passwords.items():
        uri = broken.get(component) or (
            f"mongodb://{user_name(component)}:{quote_plus(password)}@mongo:27017/"
            "?replicaSet=rs0&authSource=admin"
        )
        (directory / f"mongo_uri_{component}").write_text(uri.format(pw=password), "utf-8")


def _assert_no_secret(result: Any, secrets_: list[str]) -> None:
    text = result.output
    if result.exception is not None:
        text += "".join(traceback.format_exception(result.exception))
    for secret in secrets_:
        assert secret not in text


@pytest.mark.parametrize(
    "broken",
    [
        "mongodb://collector_projector:{pw}@mongo:notaport/?authSource=admin",
        "mongodb://collector_projector:{pw}@@mongo/",
        "mongodb://collector_other:{pw}@mongo/?authSource=admin",
        "mongodb://collector_projector:{pw}@mongo/?authSource=collector",
        "mongodb://collector_projector:{pw}@mongo/?unknownOption={pw}",
        "http://collector_projector:{pw}@mongo/",
    ],
)
def test_broken_uri_secret_never_leaks_password(
    monkeypatch: pytest.MonkeyPatch,
    mongo_server: Any,
    mongo_db: Db,
    tmp_path: Path,
    broken: str,
) -> None:
    _cli_env(monkeypatch, mongo_server, mongo_db)
    passwords = {c: "Pw" + secrets.token_hex(10) for c in USER_COMPONENTS}
    _write_secrets(tmp_path, passwords, {"projector": broken})
    result = runner.invoke(app, ["db", "ensure-mongo", "--users", "--secrets-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "mongo_uri_projector" in result.stderr
    _assert_no_secret(result, [*passwords.values(), mongo_server.password])


@pytest.mark.usefixtures("mongo_users_cleanup")
def test_server_side_user_failure_does_not_leak_password(
    monkeypatch: pytest.MonkeyPatch,
    mongo_server: Any,
    mongo_db: Db,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Пароль, який сервер відхиляє (SASLprep: заборонений control-символ), → createUser падає
    на сервері; CLI дає exit 1 без пароля у stdout/stderr/traceback/логах."""
    _cli_env(monkeypatch, mongo_server, mongo_db)
    passwords = {c: "Pw" + secrets.token_hex(10) for c in USER_COMPONENTS}
    bad_password = "Pw" + secrets.token_hex(10) + ""
    passwords["projector"] = bad_password
    _write_secrets(tmp_path, passwords, {})
    result = runner.invoke(app, ["db", "ensure-mongo", "--users", "--secrets-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "collector_projector" in result.stderr
    _assert_no_secret(result, [p.rstrip("") for p in passwords.values()])
    _assert_no_secret(result, [mongo_server.password])
    assert bad_password.rstrip("") not in caplog.text


def test_credential_repr_and_str_hide_password() -> None:
    password = "Pw" + secrets.token_hex(10)
    cred = MongoUserCredential("projector", password)
    assert password not in repr(cred)
    assert password not in str(cred)
    assert password not in f"{cred!r} {cred}"
