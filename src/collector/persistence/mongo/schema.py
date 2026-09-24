"""Collections §9.2 і маніфест обов'язкових indexes — єдине джерело для `ensure-mongo --indexes`.

Collections — стандартні (не time-series, без sharding, §9.2). Створюються міграцією
`migrations/mongo/0001_*` разом із validators; `ensure_indexes` лише створює відсутні indexes
і **звітує** (а не видаляє) зайві — index budget і `$indexStats` review (§9.2): зайвий index
прибирається свідомо, новою міграцією або вручну за runbook.

Назви `catalog_item_id`/`seller_id`/`parent_item_id`/`content_version`/`published_at` узято
дослівно з §9.2 (узгодження з контрактами WP-07/WP-09 — відкрите питання картки WP-01B).
`{entity_uuid: 1}` для current collections — це `_id` (UUID сутності, `current_document_base`),
окремий index не потрібен.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.database import Database

CATALOG_ITEMS_CURRENT = "catalog_items_current"
CATALOG_OFFERS_CURRENT = "catalog_offers_current"
VEHICLE_LISTINGS_CURRENT = "vehicle_listings_current"
SELLERS_CURRENT = "sellers_current"
ENTITY_PROJECTION_VERSIONS = "entity_projection_versions"
CATALOG_OFFER_OBSERVATIONS = "catalog_offer_observations"
VEHICLE_OBSERVATIONS = "vehicle_observations"
CONTACT_OBSERVATIONS = "contact_observations"
PRODUCT_REVIEWS = "product_reviews"
PRODUCT_QUESTIONS = "product_questions"
APPLIED_PROJECTION_RECEIPTS = "applied_projection_receipts"

CURRENT_COLLECTIONS: tuple[str, ...] = (
    CATALOG_ITEMS_CURRENT,
    CATALOG_OFFERS_CURRENT,
    VEHICLE_LISTINGS_CURRENT,
    SELLERS_CURRENT,
)
OBSERVATION_COLLECTIONS: tuple[str, ...] = (
    CATALOG_OFFER_OBSERVATIONS,
    VEHICLE_OBSERVATIONS,
    CONTACT_OBSERVATIONS,
)
REVIEW_COLLECTIONS: tuple[str, ...] = (PRODUCT_REVIEWS, PRODUCT_QUESTIONS)
DOMAIN_COLLECTIONS: tuple[str, ...] = (
    CATALOG_ITEMS_CURRENT,
    CATALOG_OFFERS_CURRENT,
    ENTITY_PROJECTION_VERSIONS,
    CATALOG_OFFER_OBSERVATIONS,
    PRODUCT_REVIEWS,
    PRODUCT_QUESTIONS,
    VEHICLE_LISTINGS_CURRENT,
    VEHICLE_OBSERVATIONS,
    SELLERS_CURRENT,
    CONTACT_OBSERVATIONS,
    APPLIED_PROJECTION_RECEIPTS,
)
"""Усі 11 collections §9.2 у порядку ТЗ."""

MIGRATIONS_COLLECTION = "schema_migrations"
"""Службова collection застосованих Mongo-міграцій (не domain, runtime-ролі її не бачать)."""

DEFAULT_INDEX = "_id_"


@dataclass(frozen=True, slots=True)
class IndexSpec:
    """Один index маніфесту; `name` — явний, стабільний (порівняння з `index_information`)."""

    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False

    def matches(self, info: Mapping[str, Any]) -> bool:
        """Чи збігається наявний index (з `index_information()`) за ключами й унікальністю."""
        return index_keys(info) == self.keys and bool(info.get("unique", False)) == self.unique


def index_keys(info: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    """Ключі index з `index_information()` у формі маніфесту (напрям — int)."""
    return tuple((str(k), int(v)) for k, v in info.get("key", ()))


def _manifest() -> tuple[IndexSpec, ...]:
    specs: list[IndexSpec] = []
    source_item = (("source.source_id", ASCENDING), ("source.source_item_id", ASCENDING))
    task = (("projection_task_id", ASCENDING),)
    for name in CURRENT_COLLECTIONS:
        specs.append(IndexSpec(name, "ux_source_item", source_item, unique=True))
        specs.append(IndexSpec(name, "ix_last_seen", (("last_seen_at", DESCENDING),)))
    specs.append(
        IndexSpec(
            CATALOG_OFFERS_CURRENT,
            "ix_catalog_item_last_seen",
            (("catalog_item_id", ASCENDING), ("last_seen_at", DESCENDING)),
        )
    )
    specs.append(
        IndexSpec(
            ENTITY_PROJECTION_VERSIONS,
            "ux_entity_version",
            (("entity_uuid", ASCENDING), ("projection_version", ASCENDING)),
            unique=True,
        )
    )
    specs.append(IndexSpec(ENTITY_PROJECTION_VERSIONS, "ux_projection_task", task, unique=True))
    for name in OBSERVATION_COLLECTIONS:
        specs.append(IndexSpec(name, "ux_projection_task", task, unique=True))
    for name in (CATALOG_OFFER_OBSERVATIONS, VEHICLE_OBSERVATIONS):
        specs.append(
            IndexSpec(
                name,
                "ix_entity_observed",
                (("entity_uuid", ASCENDING), ("observed_at", DESCENDING)),
            )
        )
    specs.append(
        IndexSpec(
            CONTACT_OBSERVATIONS,
            "ix_seller_observed",
            (("seller_id", ASCENDING), ("observed_at", DESCENDING)),
        )
    )
    for name in REVIEW_COLLECTIONS:
        specs.append(
            IndexSpec(
                name,
                "ux_source_item_content",
                (*source_item, ("content_version", ASCENDING)),
                unique=True,
            )
        )
        specs.append(
            IndexSpec(
                name,
                "ix_parent_published",
                (("parent_item_id", ASCENDING), ("published_at", DESCENDING)),
            )
        )
    specs.append(IndexSpec(APPLIED_PROJECTION_RECEIPTS, "ux_projection_task", task, unique=True))
    # Keyset-курсор reconciler-а/`list_receipts` (§15): стабільний compound sort + `_id`.
    specs.append(
        IndexSpec(
            APPLIED_PROJECTION_RECEIPTS,
            "ix_committed_cursor",
            (("committed_at", ASCENDING), ("_id", ASCENDING)),
        )
    )
    return tuple(specs)


INDEX_MANIFEST: tuple[IndexSpec, ...] = _manifest()


@dataclass(slots=True)
class IndexReport:
    """Результат `ensure_indexes`: `conflicts` → помилка CLI, `extra` → лише звіт."""

    created: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def ensure_indexes(
    db: Database[dict[str, Any]], manifest: Sequence[IndexSpec] = INDEX_MANIFEST
) -> IndexReport:
    """Створює відсутні indexes маніфесту; звітує зайві та конфлікти. Ідемпотентно.

    Конфлікт — index з тим самим ім'ям, але іншими ключами/унікальністю, або ті самі ключі під
    іншим ім'ям: Mongo сам відмовив би `createIndex`, а мовчки перейменовувати/перебудовувати
    unique index на живих даних не можна. Зайвий index — будь-який index керованої collection,
    якого немає в маніфесті (крім `_id_`); у звіті — з лічильником `$indexStats.accesses.ops`.
    """
    report = IndexReport()
    by_collection: dict[str, list[IndexSpec]] = {}
    for spec in manifest:
        by_collection.setdefault(spec.collection, []).append(spec)
    for collection, specs in by_collection.items():
        coll = db[collection]
        info = coll.index_information()
        missing: list[IndexSpec] = []
        for spec in specs:
            label = f"{collection}.{spec.name}"
            if spec.name in info:
                if spec.matches(info[spec.name]):
                    report.present.append(label)
                else:
                    report.conflicts.append(f"{label}: ключі/unique відрізняються від маніфесту")
                continue
            clash = [n for n, i in info.items() if index_keys(i) == spec.keys]
            if clash:
                report.conflicts.append(f"{label}: ті самі ключі вже під іменем {clash[0]!r}")
                continue
            missing.append(spec)
        if missing:
            # Один `createIndexes` на collection: на RS кожна команда — окремий two-phase build.
            coll.create_indexes(
                [IndexModel(list(s.keys), name=s.name, unique=s.unique) for s in missing]
            )
            report.created.extend(f"{collection}.{s.name}" for s in missing)
        known = {spec.name for spec in specs} | {DEFAULT_INDEX}
        unknown = sorted(name for name in info if name not in known)
        if unknown:
            usage = _index_usage(db, collection)
            report.extra.extend(
                f"{collection}.{name} (ops={usage.get(name, 'n/a')})" for name in unknown
            )
    return report


def _index_usage(db: Database[dict[str, Any]], collection: str) -> dict[str, int]:
    stats = db[collection].aggregate([{"$indexStats": {}}])
    return {str(s["name"]): int(s.get("accesses", {}).get("ops", 0)) for s in stats}


def manifest_by_collection(
    manifest: Iterable[IndexSpec] = INDEX_MANIFEST,
) -> dict[str, dict[str, IndexSpec]]:
    """`{collection: {index_name: spec}}` — для порівняння стану БД з маніфестом у тестах/звітах."""
    result: dict[str, dict[str, IndexSpec]] = {}
    for spec in manifest:
        result.setdefault(spec.collection, {})[spec.name] = spec
    return result
