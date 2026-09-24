"""WP-01B PR1 п.6: Mongo-користувачі за компонентами (§13) — реальні логіни проти RS з auth.

Паролі компонентів генеруються в рантаймі (gitleaks: жодних літералів-секретів у тестах).
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from mongo_factories import current_document
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure

from collector.persistence.mongo.admin import apply_mongo_schema
from collector.persistence.mongo.schema import (
    CATALOG_ITEMS_CURRENT,
    ENTITY_PROJECTION_VERSIONS,
    MIGRATIONS_COLLECTION,
)
from collector.persistence.mongo.users import (
    USER_COMPONENTS,
    MongoUserCredential,
    apply_users,
    role_privileges,
    user_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("mongo_users_cleanup")]

Db = Database[dict[str, Any]]
UNAUTHORIZED = 13


@pytest.fixture
def credentials() -> list[MongoUserCredential]:
    return [MongoUserCredential(c, secrets.token_hex(12)) for c in USER_COMPONENTS]


@pytest.fixture
def schema_with_users(
    mongo_root: MongoClient[dict[str, Any]], mongo_db: Db, credentials: list[MongoUserCredential]
) -> dict[str, MongoUserCredential]:
    apply_mongo_schema(
        mongo_root, mongo_db.name, validators=True, indexes=True, credentials=credentials
    )
    return {c.component: c for c in credentials}


@pytest.fixture
def login(
    mongo_server: Any, mongo_db: Db, schema_with_users: dict[str, MongoUserCredential]
) -> Iterator[Any]:
    clients: list[MongoClient[dict[str, Any]]] = []

    def _login(component: str) -> Db:
        credential = schema_with_users[component]
        client: MongoClient[dict[str, Any]] = MongoClient(
            mongo_server.uri(credential.user, credential.password),
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=10_000,
        )
        clients.append(client)
        return client.get_database(mongo_db.name)

    yield _login
    for client in clients:
        client.close()


def _denied(action: Any) -> None:
    with pytest.raises(OperationFailure) as info:
        action()
    assert info.value.code == UNAUTHORIZED


def test_projector_writes_domain_but_cannot_delete_drop_or_change_schema(login: Any) -> None:
    db = login("projector")
    items = db[CATALOG_ITEMS_CURRENT]
    doc = current_document(item="p1")
    items.insert_one(doc)
    items.update_one({"_id": doc["_id"]}, {"$set": {"last_seen_at": doc["last_seen_at"]}})
    assert items.find_one({"_id": doc["_id"]}) is not None
    assert CATALOG_ITEMS_CURRENT in db.list_collection_names()  # listCollections (check_ready)
    _denied(lambda: items.delete_one({"_id": doc["_id"]}))
    _denied(lambda: db[ENTITY_PROJECTION_VERSIONS].delete_many({}))
    _denied(lambda: db.drop_collection(CATALOG_ITEMS_CURRENT))
    _denied(lambda: items.create_index([("core.brand", 1)], name="ix_forbidden"))
    _denied(lambda: db.command("collMod", CATALOG_ITEMS_CURRENT, validationAction="warn"))
    _denied(lambda: db[MIGRATIONS_COLLECTION].find_one({}))
    _denied(lambda: db["not_a_domain_collection"].insert_one({"x": 1}))


def test_compactor_deletes_only_version_records(login: Any, mongo_db: Db) -> None:
    mongo_db[ENTITY_PROJECTION_VERSIONS].insert_one(
        {"entity_uuid": uuid4(), "projection_task_id": 1}
    )
    doc = current_document(item="c1")
    mongo_db[CATALOG_ITEMS_CURRENT].insert_one(doc)
    db = login("compactor")
    assert db[ENTITY_PROJECTION_VERSIONS].delete_many({}).deleted_count == 1
    _denied(lambda: db[CATALOG_ITEMS_CURRENT].delete_one({"_id": doc["_id"]}))
    _denied(lambda: db.drop_collection(ENTITY_PROJECTION_VERSIONS))


@pytest.mark.parametrize("component", ["api_ro", "export_ro"])
def test_read_only_users_cannot_write(login: Any, mongo_db: Db, component: str) -> None:
    doc = current_document(item=f"ro-{component}")
    mongo_db[CATALOG_ITEMS_CURRENT].insert_one(doc)
    db = login(component)
    assert db[CATALOG_ITEMS_CURRENT].find_one({"_id": doc["_id"]}) is not None
    _denied(lambda: db[CATALOG_ITEMS_CURRENT].insert_one(current_document(item="x")))
    _denied(lambda: db[CATALOG_ITEMS_CURRENT].update_one({}, {"$set": {"core": {}}}))
    _denied(lambda: db[CATALOG_ITEMS_CURRENT].delete_one({}))


def test_apply_users_is_idempotent_and_rotates_password(
    mongo_root: MongoClient[dict[str, Any]],
    mongo_server: Any,
    mongo_db: Db,
    schema_with_users: dict[str, MongoUserCredential],
) -> None:
    rotated = [MongoUserCredential(c, secrets.token_hex(12)) for c in USER_COMPONENTS]
    assert apply_users(mongo_root, mongo_db.name, rotated) == [
        user_name(c) for c in USER_COMPONENTS
    ]
    admin = mongo_root.admin
    for component in USER_COMPONENTS:
        info = admin.command("usersInfo", user_name(component))["users"][0]
        assert [(r["role"], r["db"]) for r in info["roles"]] == [(user_name(component), "admin")]
    roles = admin.command("rolesInfo", "collector_projector", showPrivileges=True)["roles"][0]
    expected = role_privileges(mongo_db.name)["collector_projector"]
    assert len(roles["privileges"]) == len(expected)

    old = schema_with_users["projector"]
    stale: MongoClient[dict[str, Any]] = MongoClient(
        mongo_server.uri(old.user, old.password), serverSelectionTimeoutMS=5_000
    )
    try:
        with pytest.raises(OperationFailure):
            stale.get_database(mongo_db.name)[CATALOG_ITEMS_CURRENT].find_one({})
    finally:
        stale.close()
