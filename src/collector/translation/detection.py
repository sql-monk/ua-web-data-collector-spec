"""Визначення мови статті й сегментів (§5.4, §8, §12.2, R-08; U-2).

Порядок для статті: source-declared metadata → `lang` кореневого елемента → classifier.
Для сегмента: `lang` найближчого предка → короткий сегмент (< порогу) успадковує мову
статті → classifier. Невпевнений результат не відкидається: рішення зберігає кандидата,
confidence і `uncertain=True` (quality flag `low_language_confidence`).

Classifier — `lingua-language-detector` по `uk` + 16 + extra **і sentinel-мовах** (gate 3,
R-4): classifier, обмежений лише підтримуваними мовами, не здатен повернути мову поза
набором і мовчки видає найближчу (pt → es, bg → ru, nb → nl). Sentinel — європейські мови,
близькі до підтримуваних (`SENTINEL_LANGUAGES`); їхній результат повертається як є, і план
позначає сегмент `language_unsupported`. Якщо поруч є підтримувана мова з помітною
confidence (близькі варіанти, напр. bs/hr), береться підтримувана з `uncertain=True`. Усі
75 мов lingua не беруться: у заміру український текст визначався як `kk` з confidence 1.0.
Мова з metadata/`lang` поза набором теж лишається як є.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Literal, Protocol

from lingua import IsoCode639_1, LanguageDetectorBuilder

from collector.translation.languages import (
    TARGET_LANGUAGE,
    normalize_language_tag,
    supported_source_languages,
)
from collector.translation.normalize import normalize_text

DetectionMethod = Literal["metadata", "lang_attribute", "classifier", "inherited", "none"]

DEFAULT_SHORT_SEGMENT_CHARS: Final = 30
DEFAULT_MIN_CONFIDENCE: Final = 0.6
# Якщо top-мова непідтримувана, а підтримувана мова має confidence не нижче цього порогу,
# текст вважається близьким варіантом підтримуваної мови (з `uncertain=True`).
DEFAULT_NEIGHBOUR_CONFIDENCE: Final = 0.3
# Мови, які classifier має розпізнавати, щоб не видавати їх за найближчу підтримувану.
SENTINEL_LANGUAGES: Final = frozenset(
    {"be", "bg", "bs", "cy", "da", "el", "eu", "fi", "ga", "is", "mk", "nb", "nn", "pt", "sq",
     "sr", "sv", "tr"}
)  # fmt: skip


def classifier_languages(supported: Iterable[str] | None = None) -> frozenset[str]:
    """Набір моделей classifier-а: підтримувані + `uk` + sentinel."""
    base = supported_source_languages() if supported is None else frozenset(supported)
    return base | {TARGET_LANGUAGE} | SENTINEL_LANGUAGES


class LanguageClassifier(Protocol):
    """`ranked(text)` → [(ISO 639-1, confidence 0..1)] за спаданням; `classify` — перший."""

    def classify(self, text: str) -> tuple[str | None, float]: ...

    def ranked(self, text: str) -> list[tuple[str, float]]: ...


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    language: str | None
    confidence: float
    method: DetectionMethod
    uncertain: bool = False
    candidate: str | None = None


class LinguaClassifier:
    """Offline classifier на `lingua` (моделі — у wheel, без мережі).

    `languages=None` — продакшн-набір `classifier_languages()` (R-4); явний набір — для
    вузьких перевірок.
    """

    def __init__(self, languages: Iterable[str] | None = None) -> None:
        codes = sorted(classifier_languages() if languages is None else set(languages))
        isos = [IsoCode639_1.from_str(code) for code in codes]
        self.languages: frozenset[str] = frozenset(codes)
        self._detector = LanguageDetectorBuilder.from_iso_codes_639_1(*isos).build()

    def ranked(self, text: str) -> list[tuple[str, float]]:
        values = self._detector.compute_language_confidence_values(text)
        return [
            (value.language.iso_code_639_1.name.lower(), float(value.value))
            for value in values
            if value.value > 0.0
        ]

    def classify(self, text: str) -> tuple[str | None, float]:
        ranked = self.ranked(text)
        return ranked[0] if ranked else (None, 0.0)


@lru_cache(maxsize=4)
def lingua_classifier(languages: frozenset[str] | None = None) -> LinguaClassifier:
    """Кешований classifier на процес (побудова детектора дорожча за класифікацію)."""
    return LinguaClassifier(languages)


def detect_article_language(
    *,
    declared: str | None,
    root_lang: str | None,
    text: str,
    classifier: LanguageClassifier,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    supported: frozenset[str] | None = None,
) -> LanguageDecision:
    declared_code = normalize_language_tag(declared)
    if declared_code is not None:
        return LanguageDecision(declared_code, 1.0, "metadata")
    root_code = normalize_language_tag(root_lang)
    if root_code is not None:
        return LanguageDecision(root_code, 1.0, "lang_attribute")
    if not normalize_text(text):
        return LanguageDecision(None, 0.0, "none", uncertain=True)
    decision = _classify(text, classifier, supported, min_confidence)
    if decision is None:
        return LanguageDecision(None, 0.0, "classifier", uncertain=True)
    return decision


def detect_segment_language(
    text: str,
    *,
    lang_attribute: str | None,
    article: LanguageDecision,
    classifier: LanguageClassifier,
    short_segment_chars: int = DEFAULT_SHORT_SEGMENT_CHARS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    supported: frozenset[str] | None = None,
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
    decision = _classify(text, classifier, supported, min_confidence)
    # Впевнене рішення або близький підтримуваний варіант (candidate ≠ language, uncertain).
    if decision is not None and (
        decision.confidence >= min_confidence or decision.candidate != decision.language
    ):
        return decision
    # Невпевненість зберігається: мова статті як робоче рішення + кандидат classifier-а.
    candidate = decision.candidate if decision is not None else None
    confidence = decision.confidence if decision is not None else 0.0
    return LanguageDecision(article.language, confidence, "inherited", True, candidate)


def _classify(
    text: str,
    classifier: LanguageClassifier,
    supported: frozenset[str] | None,
    min_confidence: float,
) -> LanguageDecision | None:
    """Рішення classifier-а з урахуванням підтримуваного набору (див. docstring модуля)."""
    ranked = classifier.ranked(text)
    if not ranked:
        return None
    language, confidence = ranked[0]
    if supported is None:
        return LanguageDecision(
            language, confidence, "classifier", confidence < min_confidence, language
        )
    if language == TARGET_LANGUAGE or language in supported:
        # Confidence серед підтримуваних мов: частка, віддана непідтримуваним близьким
        # варіантам (hr/bs, nl/af), не робить впевнений хорватський текст «невпевненим».
        mass = sum(v for code, v in ranked if code == TARGET_LANGUAGE or code in supported)
        confidence = confidence / mass if mass > 0 else confidence
        return LanguageDecision(
            language, confidence, "classifier", confidence < min_confidence, language
        )
    neighbour = next(
        ((code, value) for code, value in ranked if code in supported or code == TARGET_LANGUAGE),
        None,
    )
    if neighbour is not None and neighbour[1] >= DEFAULT_NEIGHBOUR_CONFIDENCE:
        return LanguageDecision(neighbour[0], neighbour[1], "classifier", True, language)
    # Мова поза набором провайдера: повертається як є → план дасть `language_unsupported`.
    return LanguageDecision(
        language, confidence, "classifier", confidence < min_confidence, language
    )
