"""Визначення мови статті й сегментів (§5.4, §8, §12.2, R-08; U-2).

Порядок для статті: source-declared metadata → `lang` кореневого елемента → classifier.
Для сегмента: `lang` найближчого предка → короткий сегмент (< порогу) успадковує мову
статті → classifier. Classifier — `lingua-language-detector`, обмежений `uk` + 16 основних +
додатковими мовами (default `ru`, `ca`); невпевнений результат не відкидається: рішення
зберігає кандидата, confidence і `uncertain=True` (quality flag `low_language_confidence`).
Мова з metadata/`lang` поза підтримуваним набором лишається як є — план позначить сегмент
`language_unsupported`, а не «перекладе» його як іншу мову.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Literal, Protocol

from lingua import IsoCode639_1, LanguageDetectorBuilder

from collector.translation.languages import normalize_language_tag
from collector.translation.normalize import normalize_text

DetectionMethod = Literal["metadata", "lang_attribute", "classifier", "inherited", "none"]

DEFAULT_SHORT_SEGMENT_CHARS: Final = 30
DEFAULT_MIN_CONFIDENCE: Final = 0.6


class LanguageClassifier(Protocol):
    """`classify(text)` → (ISO 639-1 або `None`, confidence 0..1)."""

    def classify(self, text: str) -> tuple[str | None, float]: ...


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    language: str | None
    confidence: float
    method: DetectionMethod
    uncertain: bool = False
    candidate: str | None = None


class LinguaClassifier:
    """Offline classifier на `lingua` (моделі — у wheel, без мережі)."""

    def __init__(self, languages: Iterable[str]) -> None:
        codes = sorted(set(languages))
        isos = [IsoCode639_1.from_str(code) for code in codes]
        self.languages: frozenset[str] = frozenset(codes)
        self._detector = LanguageDetectorBuilder.from_iso_codes_639_1(*isos).build()

    def classify(self, text: str) -> tuple[str | None, float]:
        values = self._detector.compute_language_confidence_values(text)
        if not values or values[0].value <= 0.0:
            return None, 0.0
        top = values[0]
        return top.language.iso_code_639_1.name.lower(), float(top.value)


@lru_cache(maxsize=4)
def lingua_classifier(languages: frozenset[str]) -> LinguaClassifier:
    """Кешований classifier на процес (побудова детектора дорожча за класифікацію)."""
    return LinguaClassifier(languages)


def detect_article_language(
    *,
    declared: str | None,
    root_lang: str | None,
    text: str,
    classifier: LanguageClassifier,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> LanguageDecision:
    declared_code = normalize_language_tag(declared)
    if declared_code is not None:
        return LanguageDecision(declared_code, 1.0, "metadata")
    root_code = normalize_language_tag(root_lang)
    if root_code is not None:
        return LanguageDecision(root_code, 1.0, "lang_attribute")
    if not normalize_text(text):
        return LanguageDecision(None, 0.0, "none", uncertain=True)
    language, confidence = classifier.classify(text)
    uncertain = language is None or confidence < min_confidence
    return LanguageDecision(language, confidence, "classifier", uncertain, candidate=language)


def detect_segment_language(
    text: str,
    *,
    lang_attribute: str | None,
    article: LanguageDecision,
    classifier: LanguageClassifier,
    short_segment_chars: int = DEFAULT_SHORT_SEGMENT_CHARS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> LanguageDecision:
    attribute_code = normalize_language_tag(lang_attribute)
    if attribute_code is not None:
        return LanguageDecision(attribute_code, 1.0, "lang_attribute")
    inherited = LanguageDecision(
        article.language, article.confidence, "inherited", article.uncertain, article.candidate
    )
    normalized = normalize_text(text)
    if len(normalized) < short_segment_chars or not any(ch.isalpha() for ch in normalized):
        return inherited
    language, confidence = classifier.classify(text)
    if language is not None and confidence >= min_confidence:
        return LanguageDecision(language, confidence, "classifier", candidate=language)
    # Невпевненість зберігається: мова статті як робоче рішення + кандидат classifier-а.
    return LanguageDecision(article.language, confidence, "inherited", True, language)
