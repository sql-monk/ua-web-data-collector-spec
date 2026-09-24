"""Спільні фікстури unit-тестів перекладу (WP-04)."""

from __future__ import annotations

import pytest

from collector.translation.detection import LinguaClassifier, lingua_classifier
from collector.translation.glossary import Glossary, load_glossary
from collector.translation.languages import TARGET_LANGUAGE, supported_source_languages


@pytest.fixture(scope="session")
def classifier() -> LinguaClassifier:
    """Offline-classifier, обмежений `uk` + 16 + default extra (формулювання п.3 картки).

    Продакшн-набір (`classifier_languages()`: + sentinel-мови, gate 3 R-4) — фікстура
    `production_classifier`; модулі планера/pipeline перевизначають `classifier` на неї.
    """
    return lingua_classifier(supported_source_languages() | {TARGET_LANGUAGE})


@pytest.fixture(scope="session")
def production_classifier() -> LinguaClassifier:
    return lingua_classifier(None)


@pytest.fixture(scope="session")
def glossary() -> Glossary:
    return load_glossary()
