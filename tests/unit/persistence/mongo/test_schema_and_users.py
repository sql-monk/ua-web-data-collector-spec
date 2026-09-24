"""Маніфест indexes §9.2 і ролі/секрети Mongo-користувачів §13 — без БД."""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import quote_plus

import pytest

from collector.persistence.mongo.schema import (
    APPLIED_PROJECTION_RECEIPTS,
    CATALOG_OFFER_OBSERVATIONS,
    CATALOG_OFFERS_CURRENT,
    CONTACT_OBSERVATIONS,
    CURRENT_COLLECTIONS,
    DOMAIN_COLLECTIONS,
    ENTITY_PROJECTION_VERSIONS,
    INDEX_MANIFEST,
    PRODUCT_QUESTIONS,
    PRODUCT_REVIEWS,
    VEHICLE_OBSERVATIONS,
    manifest_by_collection,
)
from collector.persistence.mongo.users import (
    USER_COMPONENTS,
    MongoUserCredential,
    MongoUserError,
    credential_from_uri,
    load_user_credentials,
    role_privileges,
    user_secrets_dir,
)

SOURCE_ITEM = (("source.source_id", 1), ("source.source_item_id", 1))
TASK = (("projection_task_id", 1),)


def _has(collection: str, keys: tuple[tuple[str, int], ...], *, unique: bool) -> bool:
    return any(
        s.collection == collection and s.keys == keys and s.unique == unique for s in INDEX_MANIFEST
    )


def test_all_eleven_collections_of_section_9_2() -> None:
    assert len(set(DOMAIN_COLLECTIONS)) == 11
    assert set(manifest_by_collection()) <= set(DOMAIN_COLLECTIONS)


def test_manifest_contains_every_mandatory_index_of_section_9_2() -> None:
    for name in CURRENT_COLLECTIONS:
        assert _has(name, SOURCE_ITEM, unique=True), name
        assert _has(name, (("last_seen_at", -1),), unique=False), name
    evp = ENTITY_PROJECTION_VERSIONS
    assert _has(evp, (("entity_uuid", 1), ("projection_version", 1)), unique=True)
    for name in (evp, CATALOG_OFFER_OBSERVATIONS, VEHICLE_OBSERVATIONS, CONTACT_OBSERVATIONS):
        assert _has(name, TASK, unique=True), name
    assert _has(APPLIED_PROJECTION_RECEIPTS, TASK, unique=True)
    for name in (PRODUCT_REVIEWS, PRODUCT_QUESTIONS):
        assert _has(name, (*SOURCE_ITEM, ("content_version", 1)), unique=True), name
        assert _has(name, (("parent_item_id", 1), ("published_at", -1)), unique=False), name
    for name in (CATALOG_OFFER_OBSERVATIONS, VEHICLE_OBSERVATIONS):
        assert _has(name, (("entity_uuid", 1), ("observed_at", -1)), unique=False), name
    assert _has(
        CATALOG_OFFERS_CURRENT, (("catalog_item_id", 1), ("last_seen_at", -1)), unique=False
    )
    assert _has(CONTACT_OBSERVATIONS, (("seller_id", 1), ("observed_at", -1)), unique=False)


def test_manifest_names_unique_and_no_wildcard() -> None:
    pairs = [(s.collection, s.name) for s in INDEX_MANIFEST]
    assert len(pairs) == len(set(pairs))
    assert all("$**" not in key for s in INDEX_MANIFEST for key, _ in s.keys)
    assert all(s.name != "_id_" for s in INDEX_MANIFEST)


# --- users -----------------------------------------------------------------------------------


def _uri(user: str, password: str, *, options: str = "replicaSet=rs0&authSource=admin") -> str:
    return f"mongodb://{user}:{quote_plus(password)}@mongo:27017/?{options}"


def test_role_privileges_follow_section_13() -> None:
    privileges = role_privileges("collector")
    assert set(privileges) == {f"collector_{c}" for c in USER_COMPONENTS}
    forbidden = {"remove", "dropCollection", "createIndex", "collMod", "dropDatabase"}
    for grant in privileges["collector_projector"]:
        assert not set(grant["actions"]) & forbidden, grant
        assert grant["resource"]["db"] == "collector"
    compactor_removes = {
        g["resource"]["collection"]
        for g in privileges["collector_compactor"]
        if "remove" in g["actions"]
    }
    assert compactor_removes == {ENTITY_PROJECTION_VERSIONS}
    for role in ("collector_api_ro", "collector_export_ro"):
        assert all(g["actions"] == ["find"] for g in privileges[role]), role
        assert {g["resource"]["collection"] for g in privileges[role]} == set(DOMAIN_COLLECTIONS)
    covered = {g["resource"]["collection"] for g in privileges["collector_projector"]}
    assert covered == {*DOMAIN_COLLECTIONS, ""}  # "" — listCollections на БД


def test_no_mongo_user_for_scheduler_fetcher_parser() -> None:
    for component in ("scheduler", "fetcher", "parser", "migrate"):
        with pytest.raises(MongoUserError):
            credential_from_uri(component, _uri(f"collector_{component}", "x"))


def test_credential_from_uri_checks_user_password_and_auth_source() -> None:
    password = secrets.token_hex(8)
    credential = credential_from_uri("projector", _uri("collector_projector", password))
    assert credential.password == password and credential.user == "collector_projector"
    assert password not in repr(credential)
    cases = [
        (_uri("collector_api_ro", password), "іншого користувача"),
        ("mongodb://collector_projector@mongo:27017/?authSource=admin", "немає пароля"),
        (_uri("collector_projector", password, options="authSource=other"), "authSource"),
        ("not a uri", "невалідний URI"),
    ]
    for uri, message in cases:
        with pytest.raises(MongoUserError, match=message) as info:
            credential_from_uri("projector", uri)
        assert password not in str(info.value)


def test_load_user_credentials_all_or_nothing(tmp_path: Path) -> None:
    passwords = {c: secrets.token_hex(8) for c in USER_COMPONENTS}
    for component in USER_COMPONENTS[:-1]:
        uri = _uri(f"collector_{component}", passwords[component])
        (tmp_path / f"mongo_uri_{component}").write_text(uri + "\n", encoding="utf-8")
    with pytest.raises(MongoUserError, match=f"mongo_uri_{USER_COMPONENTS[-1]}"):
        load_user_credentials(tmp_path)
    last = USER_COMPONENTS[-1]
    (tmp_path / f"mongo_uri_{last}").write_text(
        _uri(f"collector_{last}", passwords[last]), encoding="utf-8"
    )
    loaded = load_user_credentials(tmp_path)
    assert loaded == [MongoUserCredential(c, passwords[c]) for c in USER_COMPONENTS]


def test_user_secrets_dir_default_and_env(tmp_path: Path) -> None:
    assert user_secrets_dir({}) == Path("/run/secrets")
    assert user_secrets_dir({"COLLECTOR_MONGO_USER_SECRETS_DIR": str(tmp_path)}) == tmp_path
