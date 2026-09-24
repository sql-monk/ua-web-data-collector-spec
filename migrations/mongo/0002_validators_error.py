"""0002: validators current/receipts `warn → error` (§9.2).

Відмовляє (`InvalidDocumentsError`), якщо хоч одна collection має документи, що не проходять
validator; тоді жодна collection не перемикається. Відкат — нова forward-міграція з
`validationAction: warn`.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from collector.persistence.mongo.migrations import require_valid_then_error
from collector.persistence.mongo.schema import APPLIED_PROJECTION_RECEIPTS, CURRENT_COLLECTIONS


def upgrade(db: Database[dict[str, Any]]) -> None:
    require_valid_then_error(db, (*CURRENT_COLLECTIONS, APPLIED_PROJECTION_RECEIPTS))
