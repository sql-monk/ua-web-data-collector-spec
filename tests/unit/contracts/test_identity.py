"""§5.1/§9.3: UUIDv7, source identity проти реєстру, identity hash golden, idempotency keys."""

from __future__ import annotations

import itertools
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from collector.contracts.identity import (
    EntityId,
    NormalizedUrl,
    SourceIdentity,
    Uuid7Generator,
    entity_id_timestamp,
    fetch_idempotency_key,
    identity_hash_v1,
    new_entity_id,
    planned_at_bucket,
    raw_object_key,
    tracking_query_keys,
    translation_idempotency_key,
)
from collector.contracts.source_registry import (
    SOURCE_REGISTRY_ENV,
    SourceRegistryError,
    find_source_registry_path,
    known_source_ids,
    load_source_registry,
)

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "contracts" / "identity_golden.json"


# --- UUIDv7 -----------------------------------------------------------------------------------


def test_new_entity_id_is_uuid7_and_monotonic_within_process() -> None:
    ids = [new_entity_id() for _ in range(5_000)]
    assert all(value.version == 7 and value.variant == "specified in RFC 4122" for value in ids)
    assert all(a < b for a, b in itertools.pairwise(ids))
    assert all(str(a) < str(b) for a, b in itertools.pairwise(ids))


def test_uuid7_generator_monotonic_when_clock_frozen_or_goes_backwards() -> None:
    clock = [1_700_000_000_000]
    generator = Uuid7Generator(clock_ms=lambda: clock[0])
    first = [generator() for _ in range(5000)]  # переповнення 12-bit лічильника → +1 мс
    clock[0] -= 10_000  # годинник назад
    second = [generator() for _ in range(100)]
    ids = first + second
    assert all(a < b for a, b in itertools.pairwise(ids))
    assert entity_id_timestamp(first[0]) == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_uuid7_generator_thread_safe_unique() -> None:
    generator = Uuid7Generator()
    results: list[UUID] = []
    lock = threading.Lock()

    def worker() -> None:
        local = [generator() for _ in range(2000)]
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == len(results)


def test_entity_id_rejects_non_v7() -> None:
    adapter = TypeAdapter(EntityId)
    assert adapter.validate_python(new_entity_id()).version == 7
    with pytest.raises(ValidationError, match="UUIDv7"):
        adapter.validate_python(uuid4())
    with pytest.raises(ValueError, match="UUIDv7"):
        entity_id_timestamp(uuid4())


# --- source registry --------------------------------------------------------------------------


def test_registry_has_70_unique_valid_ids() -> None:
    registry = load_source_registry()
    ids = [entry.id for entry in registry.sources]
    assert len(ids) == 70
    assert len(set(ids)) == 70
    assert known_source_ids() == frozenset(ids)
    for entry in registry.sources:
        assert entry.id.startswith(f"{entry.kind.value}_{entry.country.lower()}_")


@pytest.mark.parametrize(
    "source_id", ["news_ua_suspilne", "vehicle_ua_auto_ria", "catalog_ua_moyo", "news_gb_bbc"]
)
def test_source_identity_accepts_registered_ids(source_id: str) -> None:
    identity = SourceIdentity(source_id=source_id, source_item_id="  item-1 ")
    assert identity.source_item_id == "item-1"


@pytest.mark.parametrize("source_id", ["news_ua_unknown", "NEWS_UA_SUSPILNE", "suspilne", ""])
def test_source_identity_rejects_unknown_ids(source_id: str) -> None:
    with pytest.raises(ValidationError):
        SourceIdentity(source_id=source_id, source_item_id="1")


def test_registry_loader_is_cached_and_env_override(tmp_path: Path) -> None:
    assert load_source_registry() is load_source_registry()
    with pytest.raises(SourceRegistryError, match=SOURCE_REGISTRY_ENV):
        find_source_registry_path({SOURCE_REGISTRY_ENV: str(tmp_path / "missing.yaml")})
    custom = tmp_path / "registry.yaml"
    custom.write_text(
        "schema_version: 1\nassessed_at: '2026-01-01'\nsources:\n"
        "  - {id: news_ua_a, kind: news, country: UA, name: A, domains: [a.ua],"
        " rating: 1, research: r.md}\n",
        encoding="utf-8",
    )
    assert find_source_registry_path({SOURCE_REGISTRY_ENV: str(custom)}) == custom
    assert known_source_ids(custom) == frozenset({"news_ua_a"})


def test_registry_rejects_duplicates(tmp_path: Path) -> None:
    dup = tmp_path / "dup.yaml"
    entry = (
        "{id: news_ua_a, kind: news, country: UA, name: A, domains: [a.ua], rating: 1, research: r}"
    )
    dup.write_text(
        f"schema_version: 1\nassessed_at: '2026-01-01'\nsources:\n  - {entry}\n  - {entry}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="дублікати"):
        load_source_registry(dup)


# --- identity hash / keys (golden) -----------------------------------------------------------


def test_identity_hash_golden() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for case in golden["identity_hash_v1"]:
        assert identity_hash_v1(case["canonical_url"], case["attributes"]) == case["expected"], (
            case["name"]
        )


def test_identity_hash_normalizes_attributes_and_ignores_order() -> None:
    base = identity_hash_v1("https://example.com/p/1", {"brand": "Bosch", "mpn": "GSB 13"})
    assert base == identity_hash_v1(
        "https://example.com/p/1", {"mpn": "gsb  13", "Brand": " bosch"}
    )
    assert base == identity_hash_v1(
        "https://example.com/p/1", {"brand": "Bosch", "mpn": "GSB 13", "x": None, "y": ""}
    )
    assert base != identity_hash_v1("https://example.com/p/2", {"brand": "Bosch", "mpn": "GSB 13"})
    assert base.startswith("v1:") and len(base) == 3 + 64


def test_fetch_and_translation_keys_golden() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for case in golden["fetch_idempotency_key"]:
        key = fetch_idempotency_key(
            case["source_id"],
            case["normalized_url"],
            datetime.fromisoformat(case["planned_at_bucket"]),
            case["request_variant"],
        )
        assert key == case["expected"], case["name"]
    for case in golden["translation_idempotency_key"]:
        key = translation_idempotency_key(
            UUID(case["article_version_id"]),
            case["target_language"],
            case["provider"],
            case["model_version"],
            case["glossary_version"],
        )
        assert key == case["expected"], case["name"]
    assert raw_object_key(b"") == golden["raw_object_key"]["empty"]
    assert raw_object_key("привіт".encode()) == golden["raw_object_key"]["pryvit_utf8"]


def test_planned_at_bucket_floors_to_bucket() -> None:
    planned = datetime(2026, 9, 1, 12, 34, 56, tzinfo=UTC)
    assert planned_at_bucket(planned, timedelta(minutes=15)) == datetime(
        2026, 9, 1, 12, 30, tzinfo=UTC
    )
    with pytest.raises(ValueError, match="aware UTC"):
        planned_at_bucket(datetime(2026, 9, 1, 12, 34), timedelta(minutes=15))
    with pytest.raises(ValueError, match="додатним"):
        planned_at_bucket(planned, timedelta(0))


def test_fetch_key_rejects_naive_bucket() -> None:
    with pytest.raises(ValueError, match="aware"):
        fetch_idempotency_key("news_ua_liga", "https://liga.net/a", datetime(2026, 1, 1), "default")


# --- NormalizedUrl ----------------------------------------------------------------------------


def test_normalized_url_rejects_tracking_params_keeps_original() -> None:
    original = "https://example.com/a?utm_source=x&id=1&fbclid=abc#frag"
    assert tracking_query_keys(original) == frozenset({"utm_source", "fbclid"})
    with pytest.raises(ValidationError, match="tracking"):
        NormalizedUrl(original=original, normalized=original)
    pair = NormalizedUrl(original=original, normalized="https://example.com/a?id=1")
    assert pair.original == original
