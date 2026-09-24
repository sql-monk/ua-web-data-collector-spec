"""Спільні фікстури unit-тестів перекладу (WP-04)."""

from __future__ import annotations

import pytest

from collector.translation.detection import LinguaClassifier, lingua_classifier
from collector.translation.glossary import Glossary, load_glossary
from collector.translation.languages import TARGET_LANGUAGE, supported_source_languages


@pytest.fixture(scope="session")
def classifier() -> LinguaClassifier:
    """Справжній offline-classifier: `uk` + 16 основних + default extra (`ru`, `ca`)."""
    return lingua_classifier(supported_source_languages() | {TARGET_LANGUAGE})


@pytest.fixture(scope="session")
def glossary() -> Glossary:
    return load_glossary()
