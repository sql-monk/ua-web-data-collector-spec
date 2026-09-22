"""Conftest тестів контрактів: робить `tests/fixtures/contracts/factories.py` імпортованим."""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "contracts"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))
