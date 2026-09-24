"""Unit-тести Mongo-шару WP-01B: робить `tests/fixtures/mongo/mongo_factories.py` імпортованим."""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "mongo"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))
