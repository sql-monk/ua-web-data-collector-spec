"""0001: усі 11 collections §9.2; validators current/receipts у режимі `warn`.

Validators — лише для collections, чиї snapshot-и вже є в `schemas/mongo/` (рішення
оркестратора п.8 картки WP-01B): усі `*_current` — `current_document_base@1.0` (корінь
відкритий для доменних полів), `applied_projection_receipts` — `applied_projection_receipt@1.0`.
Validators `entity_projection_versions`, observations, reviews/questions — перша міграція
WP-01B PR2. Indexes створює `ensure-mongo --indexes` з маніфесту
`collector.persistence.mongo.schema`.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from collector.persistence.mongo.migrations import ensure_collection, load_asset
from collector.persistence.mongo.schema import (
    APPLIED_PROJECTION_RECEIPTS,
    CURRENT_COLLECTIONS,
    DOMAIN_COLLECTIONS,
)


def upgrade(db: Database[dict[str, Any]]) -> None:
    current = load_asset(__file__, "current_document")
    receipt = load_asset(__file__, "applied_projection_receipt")
    validators = {name: current for name in CURRENT_COLLECTIONS}
    validators[APPLIED_PROJECTION_RECEIPTS] = receipt
    for name in DOMAIN_COLLECTIONS:
        ensure_collection(db, name, validator=validators.get(name), action="warn")
