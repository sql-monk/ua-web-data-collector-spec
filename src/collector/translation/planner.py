"""План перекладу статті (§5.4, FR-016, R-08, R-20; U-2): що і з якої мови перекладати.

`plan_article_translation` — чиста функція: поля за `content_access` (title/lead завжди, якщо
є; body лише для `full`/`partial` і лише наявний — відсутній body не генерується), сегменти,
мова кожного сегмента і дія: `translate` (мова з 16 основних або extra), `keep` (`uk`
проходить як є), `unsupported` (мова поза набором → quality flag `language_unsupported`,
сегмент не надсилається). `uk`-стаття без іншомовних сегментів → `not_required`.
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from collector.contracts import ContentAccess
from collector.translation.detection import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_SHORT_SEGMENT_CHARS,
    LanguageClassifier,
    LanguageDecision,
    detect_article_language,
    detect_segment_language,
)
from collector.translation.languages import TARGET_LANGUAGE, supported_source_languages
from collector.translation.segmenter import Segment, SegmentedDocument, segment_html

ArticleField = Literal["title", "lead", "body"]
SegmentAction = Literal["translate", "keep", "unsupported"]
# TODO(WP-01C PR2 п.4): замінити на `collector.contracts.TranslationQualityFlag`, коли
# контракт з'явиться; значення збігаються з мінімальним набором картки WP-01C + О-5.
QualityFlag = Literal[
    "preservation_failed", "low_language_confidence", "provider_truncated", "language_unsupported"
]

BODY_ACCESS: Final = frozenset({ContentAccess.FULL, ContentAccess.PARTIAL})


@dataclass(frozen=True, slots=True)
class ArticleText:
    """Вхід планувальника: оригінал однієї article version.

    TODO(WP-01C PR2 п.5): у PR2 будується з `NewsVersionCreatedEvent` (`original_language`,
    `content_access`) + вмісту artifact-ів title/lead/cleaned body (`collector.storage`).
    `title`/`lead` — plain text, `body_html` — cleaned HTML від WP-05, `None` — поля немає.
    """

    content_access: ContentAccess
    original_language: str | None = None
    title: str | None = None
    lead: str | None = None
    body_html: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedSegment:
    field: ArticleField
    segment: Segment
    language: LanguageDecision
    action: SegmentAction


@dataclass(frozen=True, slots=True)
class FieldPlan:
    field: ArticleField
    document: SegmentedDocument
    segments: tuple[PlannedSegment, ...]


@dataclass(frozen=True, slots=True)
class TranslationPlan:
    article_language: LanguageDecision
    fields: tuple[FieldPlan, ...]
    target_language: str = TARGET_LANGUAGE

    @property
    def segments(self) -> tuple[PlannedSegment, ...]:
        return tuple(planned for field in self.fields for planned in field.segments)

    @property
    def to_translate(self) -> tuple[PlannedSegment, ...]:
        return tuple(planned for planned in self.segments if planned.action == "translate")

    @property
    def not_required(self) -> bool:
        """§5.4: оригінал `uk` і жоден сегмент не визначено як не-`uk`."""
        return self.article_language.language == TARGET_LANGUAGE and all(
            planned.action == "keep" for planned in self.segments
        )

    @property
    def quality_flags(self) -> frozenset[QualityFlag]:
        flags: set[QualityFlag] = set()
        if any(planned.action == "unsupported" for planned in self.segments):
            flags.add("language_unsupported")
        if any(planned.language.uncertain for planned in self.segments):
            flags.add("low_language_confidence")
        return frozenset(flags)


def plan_article_translation(
    article: ArticleText,
    *,
    classifier: LanguageClassifier,
    supported_languages: Iterable[str] | None = None,
    short_segment_chars: int = DEFAULT_SHORT_SEGMENT_CHARS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_segment_chars: int | None = None,
    translate_attributes: Iterable[str] = (),
) -> TranslationPlan:
    """Будує план; жодного I/O, провайдера чи TM — лише сегментація і детекція мови."""
    supported = (
        supported_source_languages()
        if supported_languages is None
        else frozenset(supported_languages)
    )
    documents: list[tuple[ArticleField, SegmentedDocument]] = []
    heads: tuple[tuple[ArticleField, str | None], ...] = (
        ("title", article.title),
        ("lead", article.lead),
    )
    for name, text in heads:
        if text is not None and text.strip():
            documents.append((name, segment_html(html.escape(text, quote=False))))
    if article.content_access in BODY_ACCESS and article.body_html is not None:
        documents.append(
            (
                "body",
                segment_html(
                    article.body_html,
                    max_segment_chars=max_segment_chars,
                    translate_attributes=translate_attributes,
                ),
            )
        )
    body_document = next((doc for name, doc in documents if name == "body"), None)
    root_lang = body_document.root_lang if body_document is not None else None
    article_language = detect_article_language(
        declared=article.original_language,
        root_lang=root_lang,
        text=" ".join(segment.text for _, doc in documents for segment in doc.segments),
        classifier=classifier,
        min_confidence=min_confidence,
    )
    fields: list[FieldPlan] = []
    for name, document in documents:
        planned: list[PlannedSegment] = []
        for segment in document.segments:
            # `lang` кореневого елемента — мова статті, а не сегмента: сегмент класифікується.
            own_lang = None if segment.lang == document.root_lang else segment.lang
            decision = detect_segment_language(
                segment.text,
                lang_attribute=own_lang,
                article=article_language,
                classifier=classifier,
                short_segment_chars=short_segment_chars,
                min_confidence=min_confidence,
            )
            planned.append(PlannedSegment(name, segment, decision, _action(decision, supported)))
        fields.append(FieldPlan(name, document, tuple(planned)))
    return TranslationPlan(article_language, tuple(fields))


def _action(decision: LanguageDecision, supported: frozenset[str]) -> SegmentAction:
    if decision.language == TARGET_LANGUAGE:
        return "keep"
    if decision.language in supported:
        return "translate"
    return "unsupported"
