"""Conftest тестів контрактів: робить `tests/fixtures/contracts/factories.py` імпортованим."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "contracts"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))

from factories import current_document_payload  # noqa: E402

from collector.contracts.current import CurrentDocumentBase  # noqa: E402


@pytest.fixture
def current_document() -> CurrentDocumentBase:
    return CurrentDocumentBase.model_validate(current_document_payload())
